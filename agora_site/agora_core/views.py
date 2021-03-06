# Copyright (C) 2012 Eduardo Robles Elvira <edulix AT wadobo DOT com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import datetime


from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf.urls import patterns, url, include
from django.conf import settings
from django.core.urlresolvers import reverse
from django.core.mail import EmailMultiAlternatives, EmailMessage, send_mail, send_mass_mail
from django.utils import simplejson as json
from django.utils.decorators import method_decorator
from django.utils.translation import ugettext as _
from django.shortcuts import redirect, get_object_or_404, render_to_response
from django.template.loader import render_to_string
from django.views.generic import TemplateView, ListView, CreateView, RedirectView
from django.views.generic.edit import UpdateView, FormView
from django.views.i18n import set_language as django_set_language
from django import http

from actstream.actions import follow, unfollow, is_following
from actstream.models import (object_stream, election_stream, Action,
    user_stream)
from actstream.signals import action

from endless_pagination.views import AjaxListView

from haystack.query import EmptySearchQuerySet
from haystack.forms import ModelSearchForm, FacetedSearchForm
from haystack.query import SearchQuerySet
from haystack.views import SearchView as HaystackSearchView

from agora_site.agora_core.templatetags.agora_utils import get_delegate_in_agora
from agora_site.agora_core.models import Agora, Election, Profile, CastVote
from agora_site.agora_core.forms import *
from agora_site.misc.utils import *

class FormActionView(TemplateView):
    '''
    This is a TemplateView which doesn't allow get, only post calls (with
    CSRF) for security reasons.
    '''

    def go_next(self, request):
        '''
        Returns a redirect to the page that was being shown
        '''
        next = request.REQUEST.get('next', None)
        if not next:
            next = request.META.get('HTTP_REFERER', None)
        if not next:
            next = '/'
        return http.HttpResponseRedirect(next)

    def get(self, request, *args, **kwargs):
        # Nice try :-P but that can only be done via POST
        messages.add_message(self.request, messages.ERROR, _('You tried to '
            'execute an action improperly.'))
        return redirect('/')

class SetLanguageView(FormActionView):
    """
    Extends django's set_language view to save the user's language in his
    profile and do it in post (to prevent CSRF)
    """

    def post(self, request, language, *args, **kwargs):
        if request.user.is_authenticated():
            request.user.lang_code = language
            request.user.save()

        return django_set_language(self.request)

class HomeView(AjaxListView):
    '''
    Shows main page. It's different for non-logged in users and logged in users:
    for the former, we show a carousel of news nicely geolocated in a map; for
    the later, we show the user's activity stream along with the calendar of
    relevant elections and the like at the sidebar.
    '''
    template_name = 'agora_core/home_activity.html'
    template_name_logged_in = 'agora_core/home_loggedin_activity.html'
    page_template = 'agora_core/action_items_page.html'

    def get_queryset(self):
        if self.request.user.is_authenticated() and not self.request.user.is_anonymous():
            # change template
            self.template_name = self.template_name_logged_in
            return user_stream(self.request.user)
        else:
            return Action.objects.public()[:10]


class AgoraView(AjaxListView):
    '''
    Shows an agora main page
    '''
    template_name = 'agora_core/agora_activity.html'
    page_template = 'agora_core/action_items_page.html'

    def get_queryset(self):
        return object_stream(self.agora)

    def get_context_data(self, **kwargs):
        context = super(AgoraView, self).get_context_data(**kwargs)
        context['agora'] = self.agora
        return context

    def dispatch(self, *args, **kwargs):
        self.kwargs = kwargs
        username = self.kwargs["username"]
        agoraname = self.kwargs["agoraname"]

        self.agora = get_object_or_404(Agora, name=agoraname,
            creator__username=username)
        return super(AgoraView, self).dispatch(*args, **kwargs)

class AgoraBiographyView(TemplateView):
    '''
    Shows the biography of an agora
    '''
    template_name = 'agora_core/agora_bio.html'

    def get_context_data(self, username, agoraname, **kwargs):
        context = super(AgoraBiographyView, self).get_context_data(**kwargs)
        context['agora'] = agora = get_object_or_404(Agora, name=agoraname,
            creator__username=username)
        return context


class AgoraElectionsView(AjaxListView):
    '''
    Shows the list of elections of an agora
    '''
    template_name = 'agora_core/agora_elections.html'
    page_template = 'agora_core/election_list_page.html'

    def get_queryset(self):
        username = self.kwargs["username"]
        agoraname = self.kwargs["agoraname"]
        election_filter = self.kwargs["election_filter"]

        self.agora = get_object_or_404(Agora, name=agoraname,
            creator__username=username)

        election_list = self.agora.open_elections()

        if election_filter == "all":
            election_list = self.agora.all_elections()
        elif election_filter == "approved":
            election_list = self.agora.approved_elections()
        elif election_filter == "requested":
            election_list = self.agora.requested_elections()
        elif election_filter == "tallied":
            election_list = self.agora.get_tallied_elections()

        return election_list

    def get(self, request, *args, **kwargs):
        self.kwargs = kwargs
        return super(AgoraElectionsView, self).get(request, *args, **kwargs)

    def get_context_data(self, *args, **kwargs):
        context = super(AgoraElectionsView, self).get_context_data(**kwargs)
        context['agora'] = self.agora
        context['filter'] = self.kwargs["election_filter"]
        return context


class AgoraMembersView(AjaxListView):
    '''
    Shows the biography of an agora
    '''
    template_name = 'agora_core/agora_members.html'
    page_template = 'agora_core/user_list_page.html'

    def get_queryset(self):
        username = self.kwargs["username"]
        agoraname = self.kwargs["agoraname"]

        self.agora = get_object_or_404(Agora, name=agoraname,
            creator__username=username)

        member_list = self.agora.members.all()
        if self.kwargs['members_filter'] == 'delegates':
            member_list = self.agora.active_delegates()
        return member_list

    def get(self, request, *args, **kwargs):
        self.kwargs = kwargs
        return super(AgoraMembersView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(AgoraMembersView, self).get_context_data(**kwargs)
        context['agora'] = self.agora
        context['filter'] = self.kwargs["members_filter"]

        return context

class ElectionDelegatesView(AjaxListView):
    '''
    Shows the biography of an agora
    '''
    template_name = 'agora_core/election_delegates.html'
    page_template = 'agora_core/delegate_list_page.html'

    def get_queryset(self):
        username = self.kwargs["username"]
        agoraname = self.kwargs["agoraname"]
        electionname = self.kwargs["electionname"]
        self.election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)
        return self.election.get_votes_from_delegates().all()

    def get(self, request, *args, **kwargs):
        self.kwargs = kwargs
        return super(ElectionDelegatesView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(ElectionDelegatesView, self).get_context_data(**kwargs)
        context['election'] = self.election
        context['vote_form'] = VoteForm(self.request.POST, self.election)
        return context

class ElectionChooseDelegateView(AjaxListView):
    '''
    Shows the biography of an agora
    '''
    template_name = 'agora_core/election_choose_delegate.html'
    page_template = 'agora_core/delegate_list_page.html'

    def get_queryset(self):
        return self.election.get_votes_from_delegates().all()

    def get(self, request, *args, **kwargs):
        return super(ElectionChooseDelegateView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(ElectionChooseDelegateView, self).get_context_data(**kwargs)
        context['election'] = self.election
        context['delegate'] = self.delegate
        context['vote'] = self.vote
        context['vote_form'] = VoteForm(self.request.POST, self.election)
        return context

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        self.kwargs = kwargs
        username = self.kwargs["username"]
        agoraname = self.kwargs["agoraname"]
        electionname = self.kwargs["electionname"]
        delegate_username = self.kwargs["delegate_username"]
        self.election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)

        # TODO: if election is closed, show the delegate view for the agora
        # instead. Also, you cannot delegate to yourself
        #if not self.election.ballot_is_open()\
            #or delegation_username == self.request.username:
            #return http.HttpResponseRedirect(reverse('agora-delegate',
                #username, agoraname, delegate_username))

        self.delegate = get_object_or_404(User, username=delegate_username)
        self.vote = get_object_or_404(CastVote, is_counted=True,
            election=self.election, invalidated_at_date=None,
            voter=self.delegate)

        return super(ElectionChooseDelegateView, self).dispatch(*args, **kwargs)

class  ElectionVotesView(ElectionDelegatesView):
    template_name = 'agora_core/election_votes.html'

    def get_queryset(self):
        username = self.kwargs["username"]
        agoraname = self.kwargs["agoraname"]
        electionname = self.kwargs["electionname"]
        self.election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)
        return self.election.get_all_votes().all()

class CreateAgoraView(RequestCreateView):
    '''
    Creates a new agora
    '''
    template_name = 'agora_core/create_agora_form.html'
    form_class = CreateAgoraForm

    def get_success_url(self):
        '''
        After creating the agora, show it
        '''
        agora = self.object

        messages.add_message(self.request, messages.SUCCESS, _('Creation of '
            'Agora %(agoraname)s successful! Now start to configure and use '
            'it.') % dict(agoraname=agora.name))

        action.send(self.request.user, verb='created', action_object=agora,
            ipaddr=self.request.META.get('REMOTE_ADDR'),
            geolocation=json.dumps(geolocate_ip(self.request.META.get('REMOTE_ADDR'))))

        follow(self.request.user, agora, actor_only=False)

        return reverse('agora-view',
            kwargs=dict(username=agora.creator.username, agoraname=agora.name))

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(CreateAgoraView, self).dispatch(*args, **kwargs)


class CreateElectionView(RequestCreateView):
    '''
    Creates a new agora
    '''
    template_name = 'agora_core/create_election_form.html'
    form_class = CreateElectionForm

    def get_form_kwargs(self):
        form_kwargs = super(CreateElectionView, self).get_form_kwargs()
        username = self.kwargs["username"]
        agoraname = self.kwargs["agoraname"]
        form_kwargs["agora"] = self.agora = get_object_or_404(Agora, name=agoraname,
            creator__username=username)
        return form_kwargs

    def get_success_url(self):
        '''
        After creating the election, show it
        '''
        election = self.object

        extra_data = dict(electionname=election.pretty_name,
            username=election.agora.creator.username,
            agoraname=election.agora.name,
            election_url=election.url,
            agora_url=reverse('agora-view', kwargs=dict(
                username=election.agora.creator.username,
                agoraname=election.agora.name))
        )

        if election.is_approved:
            messages.add_message(self.request, messages.SUCCESS, _('Creation of '
                'Election <a href="%(election_url)s">%(electionname)s</a> in '
                '<a href="%(agora_url)s">%(username)s/%(agoraname)s</a> '
                'successful!') % extra_data)

            action.send(self.request.user, verb='created', action_object=election,
                target=election.agora, ipaddr=self.request.META.get('REMOTE_ADDR'),
            geolocation=json.dumps(geolocate_ip(self.request.META.get('REMOTE_ADDR'))))
        else:
            messages.add_message(self.request, messages.SUCCESS, _('Creation of '
                'Election <a href="%(election_url)s">%(electionname)s</a> in '
                '<a href="%(agora_url)s">%(username)s/%(agoraname)s</a> '
                'successful! Now it <strong>awaits the agora administrators '
                'approval</strong>.') % extra_data)

            action.send(self.request.user, verb='proposed', action_object=election,
                target=election.agora, ipaddr=self.request.META.get('REMOTE_ADDR'),
            geolocation=json.dumps(geolocate_ip(self.request.META.get('REMOTE_ADDR'))))

        context = get_base_email_context(self.request)

        context.update(dict(
            election=election,
            action_user_url='/%s' % election.creator.username,
        ))

        for admin in election.agora.admins.all():
            context['to'] = admin

            if not admin.email:
                continue

            email = EmailMultiAlternatives(
                subject=_('Election %s created') % election.pretty_name,
                body=render_to_string('agora_core/emails/election_created.txt',
                    context),
                to=[admin.email])

            email.attach_alternative(
                render_to_string('agora_core/emails/election_created.html',
                    context), "text/html")
            email.send()

        follow(self.request.user, election, actor_only=False)

        return reverse('election-view',
            kwargs=dict(username=election.agora.creator.username,
                agoraname=election.agora.name, electionname=election.name))

    def get_context_data(self, **kwargs):
        context = super(CreateElectionView, self).get_context_data(**kwargs)

        username = self.kwargs["username"]
        agoraname = self.kwargs["agoraname"]
        context['agora'] = get_object_or_404(Agora, name=agoraname,
            creator__username=username)
        return context

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(CreateElectionView, self).dispatch(*args, **kwargs)


class ElectionView(AjaxListView):
    '''
    Shows an election main page
    '''
    template_name = 'agora_core/election_activity.html'
    page_template = 'agora_core/action_items_page.html'

    def get_queryset(self):
        return election_stream(self.election)

    def get_context_data(self, *args, **kwargs):
        context = super(ElectionView, self).get_context_data(**kwargs)
        context['election'] = self.election
        context['vote_form'] = VoteForm(self.request.POST, self.election)

        if self.request.user.is_authenticated():
            context['vote_from_user'] = self.election.get_vote_for_voter(
                self.request.user)
        return context

    def dispatch(self, *args, **kwargs):
        self.kwargs = kwargs

        username = kwargs['username']
        agoraname = kwargs['agoraname']
        electionname = kwargs['electionname']
        self.election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)
        return super(ElectionView, self).dispatch(*args, **kwargs)


class EditElectionView(UpdateView):
    '''
    Creates a new agora
    '''
    template_name = 'agora_core/election_edit.html'
    form_class = ElectionEditForm
    model = Election

    def post(self, request, *args, **kwargs):
        if not self.election.has_perms('edit_details', self.request.user):
            messages.add_message(self.request, messages.SUCCESS, _('Sorry, but '
            'you don\'t have edit permissions on <em>%(electionname)s</em>.') %\
                dict(electionname=self.election.pretty_name))

            url = reverse('election-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name, electionname=election.name))
            return http.HttpResponseRedirect(url)
        return super(EditElectionView, self).post(request, *args, **kwargs)


    def get(self, request, *args, **kwargs):
        if not self.election.has_perms('edit_details', self.request.user):
            messages.add_message(self.request, messages.SUCCESS, _('Sorry, but '
            'you don\'t have edit permissions on <em>%(electionname)s</em>.') %\
                dict(electionname=self.election.pretty_name))

            url = reverse('election-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name, electionname=election.name))
            return http.HttpResponseRedirect(url)
        return super(EditElectionView, self).get(request, *args, **kwargs)

    def get_success_url(self):
        '''
        After creating the agora, show it
        '''
        messages.add_message(self.request, messages.SUCCESS, _('Election '
            '%(electionname)s edited.') % dict(electionname=self.election.pretty_name))

        action.send(self.request.user, verb='edited', action_object=self.election,
            ipaddr=self.request.META.get('REMOTE_ADDR'),
            geolocation=json.dumps(geolocate_ip(self.request.META.get('REMOTE_ADDR'))))

        return reverse('election-view',
            kwargs=dict(username=self.election.agora.creator.username,
                agoraname=self.election.agora.name,
                electionname=self.election.name))

    def get_object(self):
        return self.election

    def get_context_data(self, *args, **kwargs):
        context = super(EditElectionView, self).get_context_data(**kwargs)
        context['object_list'] = []
        return context

    def get_form_kwargs(self):
        kwargs = super(EditElectionView, self).get_form_kwargs()
        kwargs.update({'request': self.request})
        return kwargs

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        username = kwargs['username']
        agoraname = kwargs['agoraname']
        electionname = kwargs['electionname']
        self.election = get_object_or_404(Election, name=electionname,
            agora__name=agoraname, agora__creator__username=username)

        return super(EditElectionView, self).dispatch(*args, **kwargs)


class StartElectionView(FormActionView):
    def post(self, request, username, agoraname, electionname, *args, **kwargs):
        election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)

        if not election.has_perms('begin_election', request.user):
            messages.add_message(self.request, messages.ERROR, _('You don\'t '
                'have permission to begin the election.'))
            return self.go_next(request)

        election.voting_starts_at_date = datetime.datetime.now()
        election.create_hash()
        election.save()

        context = get_base_email_context(self.request)

        context.update(dict(
            election=election,
            election_url=reverse('election-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name, electionname=election.name)),
            agora_url=reverse('agora-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name)),
        ))

        # List of emails to send. tuples are of format:
        # 
        # (subject, text, html, from_email, recipient)
        datatuples = []

        # NOTE: for now, electorate is dynamic and just taken from the election's
        # agora members' list
        for voter in election.agora.members.all():

            if not voter.email:
                continue

            context['to'] = voter
            try:
                context['delegate'] = get_delegate_in_agora(voter, election.agora)
            except:
                pass
            datatuples.append((
                _('Vote in election %s') % election.pretty_name,
                render_to_string('agora_core/emails/election_started.txt',
                    context),
                render_to_string('agora_core/emails/election_started.html',
                    context),
                None,
                [voter.email]))

        # Also notify third party delegates
        for voter in election.agora.active_nonmembers_delegates():

            if not voter.email:
                continue

            context['to'] = voter
            datatuples.append((
                _('Vote in election %s') % election.pretty_name,
                render_to_string('agora_core/emails/election_started.txt',
                    context),
                render_to_string('agora_core/emails/election_started.html',
                    context),
                None,
                [voter.email]))

        send_mass_html_mail(datatuples)

        if not is_following(self.request.user, election):
            follow(self.request.user, election, actor_only=False)

        return self.go_next(request)

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(StartElectionView, self).dispatch(*args, **kwargs)


class StopElectionView(FormActionView):
    def post(self, request, username, agoraname, electionname, *args, **kwargs):
        election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)

        if not election.has_perms('end_election', request.user):
            messages.add_message(self.request, messages.ERROR, _('You don\'t '
                'have permission to stop the election.'))
            return self.go_next(request)

        election.voting_extended_until_date = election.voting_ends_at_date = datetime.datetime.now()
        election.save()
        election.compute_result()

        context = get_base_email_context(self.request)

        context.update(dict(
            election=election,
            election_url=reverse('election-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name, electionname=election.name)),
            agora_url=reverse('agora-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name)),
        ))

        # List of emails to send. tuples are of format:
        #
        # (subject, text, html, from_email, recipient)
        datatuples = []

        for vote in election.get_all_votes():

            if not vote.voter.email:
                continue

            context['to'] = vote.voter
            try:
                context['delegate'] = get_delegate_in_agora(vote.voter, election.agora)
            except:
                pass
            datatuples.append((
                _('Election results for %s') % election.pretty_name,
                render_to_string('agora_core/emails/election_results.txt',
                    context),
                render_to_string('agora_core/emails/election_results.html',
                    context),
                None,
                [vote.voter.email]))

        send_mass_html_mail(datatuples)

        action.send(self.request.user, verb='published results', action_object=election,
            target=election.agora, ipaddr=request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(request.META.get('REMOTE_ADDR')))

        return self.go_next(request)

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(StopElectionView, self).dispatch(*args, **kwargs)


class ArchiveElectionView(FormActionView):
    def post(self, request, username, agoraname, electionname, *args, **kwargs):
        election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)

        if not election.has_perms('archive_election', request.user):
            messages.add_message(self.request, messages.ERROR, _('You don\'t '
                'have permission to archive the election.'))
            return self.go_next(request)

        if election.archived_at_date != None:
            messages.add_message(self.request, messages.ERROR, _('Election is '
                'already archived.'))
            return self.go_next(request)

        election.archived_at_date = datetime.datetime.now()
        election.save()

        context = get_base_email_context(self.request)

        context.update(dict(
            election=election,
            election_url=reverse('election-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name, electionname=election.name)),
            agora_url=reverse('agora-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name)),
        ))

        # List of emails to send. tuples are of format:
        #
        # (subject, text, html, from_email, recipient)
        datatuples = []

        for vote in election.get_all_votes():

            if not vote.voter.email:
                continue

            context['to'] = vote.voter
            try:
                context['delegate'] = get_delegate_in_agora(vote.voter, election.agora)
            except:
                pass
            datatuples.append((
                _('Election archived: %s') % election.pretty_name,
                render_to_string('agora_core/emails/election_archived.txt',
                    context),
                render_to_string('agora_core/emails/election_archived.html',
                    context),
                None,
                [vote.voter.email]))

        send_mass_html_mail(datatuples)

        action.send(self.request.user, verb='archived', action_object=election,
            target=election.agora, ipaddr=request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(request.META.get('REMOTE_ADDR')))

        return self.go_next(request)

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(ArchiveElectionView, self).dispatch(*args, **kwargs)


class VoteView(CreateView):
    '''
    Creates a new agora
    '''
    template_name = 'agora_core/vote_form.html'
    form_class = VoteForm

    def get_form_kwargs(self):
        form_kwargs = super(VoteView, self).get_form_kwargs()
        form_kwargs["request"] = self.request
        form_kwargs["election"] = self.election
        return form_kwargs

    def get_success_url(self):
        messages.add_message(self.request, messages.SUCCESS, _('Your vote was '
            'correctly casted! Now you could share this election in Facebook, '
            'Google Plus, Twitter, etc.'))

        # NOTE: The form is in charge in this case of creating the related action

        context = get_base_email_context(self.request)

        context.update(dict(
            to=self.request.user,
            election=self.election,
            election_url=reverse('election-view',
                kwargs=dict(username=self.election.agora.creator.username,
                    agoraname=self.election.agora.name, electionname=self.election.name)),
            agora_url=reverse('agora-view',
                kwargs=dict(username=self.election.agora.creator.username,
                    agoraname=self.election.agora.name)),
        ))

        if self.request.user.email:
            email = EmailMultiAlternatives(
                subject=_('Vote casted for election %s') % self.election.pretty_name,
                body=render_to_string('agora_core/emails/vote_casted.txt',
                    context),
                to=[self.request.user.email])

            email.attach_alternative(
                render_to_string('agora_core/emails/vote_casted.html',
                    context), "text/html")
            email.send()

        if not is_following(self.request.user, self.election):
            follow(self.request.user, self.election, actor_only=False)

        return reverse('election-view',
            kwargs=dict(username=self.election.agora.creator.username,
                agoraname=self.election.agora.name, electionname=self.election.name))

    def get_context_data(self, **kwargs):
        context = super(VoteView, self).get_context_data(**kwargs)
        form = kwargs['form']
        context['vote_form'] = form
        context['election'] = form.election
        context['object_list'] = election_stream(form.election)
        return context

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        username = kwargs["username"]
        agoraname = kwargs["agoraname"]
        electionname = kwargs["electionname"]
        self.election = get_object_or_404(Election, name=electionname,
            agora__name=agoraname, agora__creator__username=username)

        # check if ballot is open
        if not self.election.ballot_is_open():
            messages.add_message(self.request, messages.ERROR, _('Sorry, '
                'election is closed and thus you cannot vote.'))
            election_url = reverse('election-view',
                kwargs=dict(username=username, agoraname=agoraname,
                    electionname=electionname))
            return http.HttpResponseRedirect(election_url)

        return super(VoteView, self).dispatch(*args, **kwargs)

class AgoraActionChooseDelegateView(FormActionView):
    def post(self, request, username, agoraname, delegate_username, *args, **kwargs):
        agora = get_object_or_404(Agora,
            name=agoraname, creator__username=username)
        delegate = get_object_or_404(User, username=delegate_username)

        if delegate_username == self.request.user.username:
            messages.add_message(self.request, messages.ERROR, _('Sorry, but '
                'you cannot delegate to yourself ;-).'))
            return self.go_next(request)

        if self.request.user not in agora.members.all():
            if not agora.has_perms('join', self.request.user):
                messages.add_message(self.request, messages.ERROR, _('Sorry, '
                    'but you cannot delegate if you\'re not a member of the '
                    'agora first.'))
                return self.go_next(request)
            # Join agora if possible
            AgoraActionJoinView().post(request, username, agoraname)

        # invalidate older votes from the same voter to the same election
        old_votes = agora.delegation_election.cast_votes.filter(
            is_direct=False, invalidated_at_date=None)
        for old_vote in old_votes:
            old_vote.invalidated_at_date = datetime.datetime.now()
            old_vote.save()

        vote = CastVote()

        vote.data = {
            "a": "delegated-vote",
            "answers": [
                {
                    "a": "plaintext-delegate",
                    "choices": [
                        {
                            'user_id': delegate.id, # id of the User in which the voter delegates
                            'username': delegate.username,
                            'user_name': delegate.first_name, # data of the User in which the voter delegates
                        }
                    ]
                }
            ],
            "election_hash": {"a": "hash/sha256/value", "value": agora.delegation_election.hash},
            "election_uuid": agora.delegation_election.uuid
        }

        vote.voter = self.request.user
        vote.election = agora.delegation_election
        vote.is_counted = self.request.user in agora.members.all()
        vote.is_direct = False
        vote.is_public = True
        vote.casted_at_date = datetime.datetime.now()
        vote.create_hash()
        vote.save()

        action.send(self.request.user, verb='delegated', action_object=vote,
            target=agora, ipaddr=request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(request.META.get('REMOTE_ADDR')))

        vote.action_id = Action.objects.filter(actor_object_id=self.request.user.id,
            verb='delegated', action_object_object_id=vote.id,
            target_object_id=agora.id).order_by('-timestamp').all()[0].id

        # TODO: send an email to the user
        messages.add_message(self.request, messages.SUCCESS, _('You delegated '
            'your vote in %(agora)s to %(username)s! Now you could share this '
            'in Facebook, Google Plus, Twitter, etc.') % dict(
                agora=agora.creator.username+'/'+agora.name,
                username=delegate.username))

        if not is_following(self.request.user, delegate):
            follow(self.request.user, delegate, actor_only=False)

        return self.go_next(request)

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(AgoraActionChooseDelegateView, self).dispatch(*args, **kwargs)

class AgoraActionJoinView(FormActionView):
    def post(self, request, username, agoraname, *args, **kwargs):
        agora = get_object_or_404(Agora,
            name=agoraname, creator__username=username)

        if not agora.has_perms('join', request.user):
            messages.add_message(request, messages.ERROR, _('Sorry, you '
                'don\'t have permission to join this agora.'))
            return self.go_next(request)

        if request.user in agora.members.all():
            messages.add_message(request, messages.ERROR, _('Guess what, you '
                'are already a member of %(agora)s!' %\
                    dict(agora=username+'/'+agoraname)))
            return self.go_next(request)

        agora.members.add(request.user)
        agora.save()

        action.send(request.user, verb='joined', action_object=agora,
            ipaddr=request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(request.META.get('REMOTE_ADDR')))

        # TODO: send an email to the user
        messages.add_message(request, messages.SUCCESS, _('You joined '
            '%(agora)s. Now you could take a look at what elections are '
            'available at this agora') % dict(
                agora=agora.creator.username+'/'+agora.name))

        if not is_following(request.user, agora):
            follow(request.user, agora, actor_only=False)

        return self.go_next(request)

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(AgoraActionJoinView, self).dispatch(*args, **kwargs)


class AgoraActionLeaveView(FormActionView):
    def post(self, request, username, agoraname, *args, **kwargs):
        agora = get_object_or_404(Agora,
            name=agoraname, creator__username=username)

        if not agora.has_perms('leave', request.user):
            messages.add_message(request, messages.ERROR, _('Sorry, you '
                'don\'t have permission to leave this agora.'))
            return self.go_next(request)

        if request.user not in agora.members.all():
            messages.add_message(request, messages.ERROR, _('Guess what, you '
                'are already not a member of %(agora)s!' %\
                    dict(agora=username+'/'+agoraname)))
            return self.go_next(request)

        agora.members.remove(request.user)
        agora.save()

        action.send(request.user, verb='left', action_object=agora,
            ipaddr=request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(request.META.get('REMOTE_ADDR')))

        # TODO: send an email to the user
        messages.add_message(request, messages.SUCCESS, _('You left '
            '%(agora)s.') % dict(agora=agora.creator.username+'/'+agora.name))

        if is_following(self.request.user, agora):
            unfollow(self.request.user, agora)

        return self.go_next(request)

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(AgoraActionLeaveView, self).dispatch(*args, **kwargs)

class AgoraActionRemoveAdminMembershipView(FormActionView):
    def post(self, request, username, agoraname, *args, **kwargs):
        agora = get_object_or_404(Agora,
            name=agoraname, creator__username=username)

        if not agora.has_perms('leave', request.user):
            messages.add_message(request, messages.ERROR, _('Sorry, you '
                'don\'t have permission to leave this agora.'))
            return self.go_next(request)

        if request.user not in agora.admins.all():
            messages.add_message(request, messages.ERROR, _('Guess what, you '
                'are already not an admin member of %(agora)s!' %\
                    dict(agora=username+'/'+agoraname)))
            return self.go_next(request)

        agora.admins.remove(request.user)
        agora.save()

        action.send(request.user, verb='removed admin membership',
            action_object=agora, ipaddr=request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(request.META.get('REMOTE_ADDR')))

        # TODO: send an email to the user
        messages.add_message(request, messages.SUCCESS, _('You removed your '
            'admin membership at %(agora)s.') % dict(agora=agora.creator.username+'/'+agora.name))

        return self.go_next(request)

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(AgoraActionRemoveAdminMembershipView, self).dispatch(*args, **kwargs)


class ElectionCommentsView(ElectionView):
    template_name = 'agora_core/election_comments.html'

    def get_queryset(self):
        return object_stream(self.election, verb='commented')

    def get_context_data(self, *args, **kwargs):
        context = super(ElectionCommentsView, self).get_context_data(*args, **kwargs)

        if self.request.user.is_authenticated():
            context['form'] = PostCommentForm(request=self.request,
                target_object=self.election)
            context['form'].helper.form_action = reverse('election-comments-post',
                kwargs=dict(username=self.election.agora.creator.username,
                    agoraname=self.election.agora.name,
                    electionname=self.election.name))
        return context

class ElectionPostCommentView(RequestCreateView):
    template_name = 'agora_core/election_comments.html'
    form_class = PostCommentForm

    def get_context_data(self, *args, **kwargs):
        context = super(ElectionPostCommentView, self).get_context_data(*args, **kwargs)
        context['election'] = self.election
        context['vote_form'] = VoteForm(self.request.POST, self.election)
        context['object_list'] = election_stream(self.election, verb='commented')
        return context

    def get_form_kwargs(self):
        kwargs = super(ElectionPostCommentView, self).get_form_kwargs()
        kwargs['target_object'] = self.election
        return kwargs

    def get_success_url(self):
        '''
        After creating the comment, post the action and show last comments
        '''
        comment = self.object

        messages.add_message(self.request, messages.SUCCESS, _('Your comment '
            'was successfully posted.'))

        action.send(self.request.user, verb='commented', target=self.election,
            action_object=comment, ipaddr=self.request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(self.request.META.get('REMOTE_ADDR')))

        if not is_following(self.request.user, self.election):
            follow(self.request.user, self.election, actor_only=False)

        return reverse('election-comments',
            kwargs=dict(username=self.election.agora.creator.username,
                agoraname=self.election.agora.name,
                electionname=self.election.name))

    def dispatch(self, *args, **kwargs):
        self.kwargs = kwargs

        username = kwargs['username']
        agoraname = kwargs['agoraname']
        electionname = kwargs['electionname']
        self.election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)
        return super(ElectionPostCommentView, self).dispatch(*args, **kwargs)


class AgoraCommentsView(AgoraView):
    template_name = 'agora_core/agora_comments.html'

    def get_queryset(self):
        return object_stream(self.agora, verb='commented')

    def get_context_data(self, *args, **kwargs):
        context = super(AgoraCommentsView, self).get_context_data(*args, **kwargs)
        context['agora'] = self.agora

        if self.request.user.is_authenticated():
            context['form'] = PostCommentForm(request=self.request,
                target_object=self.agora)
            context['form'].helper.form_action = reverse('agora-comments-post',
                kwargs=dict(username=self.agora.creator.username,
                    agoraname=self.agora.name))
        return context


class AgoraPostCommentView(RequestCreateView):
    template_name = 'agora_core/agora_comments.html'
    form_class = PostCommentForm

    def get_context_data(self, *args, **kwargs):
        context = super(AgoraPostCommentView, self).get_context_data(*args, **kwargs)
        context['agora'] = self.agora
        context['object_list'] = object_stream(self.agora, verb='commented')
        return context

    def get_form_kwargs(self):
        kwargs = super(AgoraPostCommentView, self).get_form_kwargs()
        kwargs['target_object'] = self.agora
        return kwargs

    def get_success_url(self):
        '''
        After creating the comment, post the action and show last comments
        '''
        comment = self.object

        messages.add_message(self.request, messages.SUCCESS, _('Your comment '
            'was successfully posted.'))

        action.send(self.request.user, verb='commented', target=self.agora,
            action_object=comment, ipaddr=self.request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(self.request.META.get('REMOTE_ADDR')))

        if not is_following(self.request.user, self.agora):
            follow(self.request.user, self.agora, actor_only=False)

        return reverse('agora-comments',
            kwargs=dict(username=self.agora.creator.username,
                agoraname=self.agora.name))

    def dispatch(self, *args, **kwargs):
        self.kwargs = kwargs

        username = kwargs['username']
        agoraname = kwargs['agoraname']
        self.agora = get_object_or_404(Agora, name=agoraname,
            creator__username=username)
        return super(AgoraPostCommentView, self).dispatch(*args, **kwargs)

class CancelVoteView(FormActionView):
    def post(self, request, username, agoraname, electionname, *args, **kwargs):
        election = get_object_or_404(Election,
            name=electionname, agora__name=agoraname,
            agora__creator__username=username)

        election_url=reverse('election-view',
            kwargs=dict(username=election.agora.creator.username,
                agoraname=election.agora.name, electionname=election.name))

        if not election.ballot_is_open():
            messages.add_message(self.request, messages.ERROR, _('You can\'t '
                'cancel a vote in a closed election.'))
            return http.HttpResponseRedirect(election_url)

        vote = election.get_vote_for_voter(self.request.user)

        if not vote or not vote.is_direct:
            messages.add_message(self.request, messages.ERROR, _('You can\'t '
                'didn\'t participate in this election.'))
            return http.HttpResponseRedirect(election_url)

        vote.invalidated_at_date = datetime.datetime.now()
        vote.is_counted = False
        vote.save()

        context = get_base_email_context(self.request)
        context.update(dict(
            election=election,
            election_url=election_url,
            to=vote.voter,
            agora_url=reverse('agora-view',
                kwargs=dict(username=election.agora.creator.username,
                    agoraname=election.agora.name)),
        ))

        try:
            context['delegate'] = get_delegate_in_agora(vote.voter, election.agora)
        except:
            pass

        if vote.voter.email:
            email = EmailMultiAlternatives(
                subject=_('Vote cancelled for election %s') % election.pretty_name,
                body=render_to_string('agora_core/emails/vote_cancelled.txt',
                    context),
                to=[vote.voter.email])

            email.attach_alternative(
                render_to_string('agora_core/emails/vote_cancelled.html',
                    context), "text/html")
            email.send()

        action.send(self.request.user, verb='vote cancelled', action_object=election,
            target=election.agora, ipaddr=request.META.get('REMOTE_ADDR'),
            geolocation=geolocate_ip(request.META.get('REMOTE_ADDR')))

        return http.HttpResponseRedirect(election_url)

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(CancelVoteView, self).dispatch(*args, **kwargs)

class UserView(AjaxListView):
    '''
    Shows an user main page
    '''
    template_name = 'agora_core/user_activity.html'
    page_template = 'agora_core/action_items_page.html'

    def get_queryset(self):
        return user_stream(self.user_shown)

    def get_context_data(self, *args, **kwargs):
        context = super(UserView, self).get_context_data(**kwargs)
        context['user_shown'] = self.user_shown

        context['election_items'] = []

        for election in self.user_shown.get_profile().get_participated_elections().all():
            vote = self.user_shown.get_profile().get_vote_in_election(election)
            pretty_answer = vote.get_chained_first_pretty_answer(election)
            context['election_items'] += [[election, vote, pretty_answer]]
        return context

    def dispatch(self, *args, **kwargs):
        self.kwargs = kwargs

        username = kwargs['username']
        self.user_shown = get_object_or_404(User, username=username)
        return super(UserView, self).dispatch(*args, **kwargs)

class UserBiographyView(UserView):
    template_name = 'agora_core/user_bio.html'

    def get_context_data(self, *args, **kwargs):
        context = super(UserBiographyView, self).get_context_data(**kwargs)
        context['user_shown'] = self.user_shown
        return context

    def dispatch(self, *args, **kwargs):
        self.kwargs = kwargs

        username = kwargs['username']
        self.user_shown = get_object_or_404(User, username=username)
        return super(UserView, self).dispatch(*args, **kwargs)

class UserSettingsView(UpdateView):
    '''
    Creates a new agora
    '''
    template_name = 'agora_core/user_settings.html'
    form_class = UserSettingsForm
    model = User

    def get_success_url(self):
        '''
        After creating the agora, show it
        '''
        messages.add_message(self.request, messages.SUCCESS,
            _('User settings updated.'))

        action.send(self.request.user, verb='settings updated',
            ipaddr=self.request.META.get('REMOTE_ADDR'),
            geolocation=json.dumps(geolocate_ip(self.request.META.get('REMOTE_ADDR'))))

        return reverse('user-view',
            kwargs=dict(username=self.request.user.username))

    def get_object(self):
        return self.request.user

    def get_context_data(self, *args, **kwargs):
        context = super(UserSettingsView, self).get_context_data(**kwargs)
        context['user_shown'] = self.request.user
        return context

    def get_form_kwargs(self):
        kwargs = super(UserSettingsView, self).get_form_kwargs()
        kwargs.update({'request': self.request})
        return kwargs

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(UserSettingsView, self).dispatch(*args, **kwargs)

class UserElectionsView(AjaxListView):
    '''
    Shows the list of elections of an agora
    '''
    template_name = 'agora_core/user_elections.html'
    page_template = 'agora_core/election_list_page.html'

    def get_queryset(self):
        username = self.kwargs["username"]
        election_filter = self.kwargs["election_filter"]

        self.user_shown = get_object_or_404(User, username=username)

        election_list = self.user_shown.get_profile().get_open_elections()

        if election_filter == "participated":
            election_list = self.user_shown.get_profile().get_participated_elections()
        elif election_filter == "requested":
            election_list = self.user_shown.get_profile().get_requested_elections()

        return election_list

    def get(self, request, *args, **kwargs):
        self.kwargs = kwargs
        return super(UserElectionsView, self).get(request, *args, **kwargs)

    def get_context_data(self, *args, **kwargs):
        context = super(UserElectionsView, self).get_context_data(**kwargs)
        context['user_shown'] = self.user_shown
        context['filter'] = self.kwargs["election_filter"]
        return context




class AgoraAdminView(UpdateView):
    '''
    Creates a new agora
    '''
    template_name = 'agora_core/agora_admin.html'
    form_class = AgoraAdminForm
    model = Agora

    def post(self, request, *args, **kwargs):
        if not self.agora.has_perms('admin', self.request.user):
            messages.add_message(self.request, messages.SUCCESS, _('Sorry, but '
            'you don\'t have admin permissions on %(agoraname)s.') %\
                dict(agoraname=self.agora.name))

            url = reverse('agora-view',
                kwargs=dict(username=agora.creator.username, agoraname=agora.name))
            return http.HttpResponseRedirect(url)
        return super(AgoraAdminView, self).post(request, *args, **kwargs)


    def get(self, request, *args, **kwargs):
        if not self.agora.has_perms('admin', self.request.user):
            messages.add_message(self.request, messages.SUCCESS, _('Sorry, but '
            'you don\'t have admin permissions on %(agoraname)s.') %\
                dict(agoraname=self.agora.name))

            url = reverse('agora-view',
                kwargs=dict(username=self.agora.creator.username, agoraname=self.agora.name))
            return http.HttpResponseRedirect(url)
        return super(AgoraAdminView, self).get(request, *args, **kwargs)

    def get_success_url(self):
        '''
        After creating the agora, show it
        '''
        agora = self.object

        messages.add_message(self.request, messages.SUCCESS, _('Agora settings '
            'changed for %(agoraname)s.') % dict(agoraname=self.agora.name))

        action.send(self.request.user, verb='changed settings', action_object=agora,
            ipaddr=self.request.META.get('REMOTE_ADDR'),
            geolocation=json.dumps(geolocate_ip(self.request.META.get('REMOTE_ADDR'))))

        return reverse('agora-view',
            kwargs=dict(username=agora.creator.username, agoraname=agora.name))

    def get_object(self):
        return self.agora

    def get_form_kwargs(self):
        kwargs = super(AgoraAdminView, self).get_form_kwargs()
        kwargs.update({'request': self.request})
        return kwargs

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        username = kwargs['username']
        agoraname = kwargs['agoraname']
        self.agora = get_object_or_404(Agora, name=agoraname,
            creator__username=username)

        return super(AgoraAdminView, self).dispatch(*args, **kwargs)


class SearchView(AjaxListView, HaystackSearchView):
    '''
    Generic search view for all kinds of indexed objects
    '''
    template_name = 'search/search.html'
    page_template = 'search/search_page.html'
    form_class = ModelSearchForm
    load_all = True
    searchqueryset = None
    searchmodel = None

    def get_queryset(self):
        return self.results

    def get_context_data(self, **kwargs):
        context = super(SearchView, self).get_context_data(**kwargs)
        context.update({
            'query': self.query,
            'form': self.form,
            'num_results': self.get_queryset().count()
        })
        return context

    def get(self, request, *args, **kwargs):
        self.request = request
        # [('agora_core.agora', u'Agoras'), ('agora_core.election', u'Elections'), ('agora_core.profile', u'Profiles')]
        self.form = self.build_form()
        self.query = self.get_query()

        if self.searchmodel != None and not self.query:
            if self.searchmodel == "agoras":
                self.results = Agora.objects.all()
            elif self.searchmodel == "elections":
                self.results = Election.objects.exclude(url__startswith="http://example.com/delegation/has/no/url/")
            elif self.searchmodel == "profiles":
                self.results = Profile.objects.all()
        else:
            self.results = self.get_results()
        return super(SearchView, self).get(request, *args, **kwargs)


class ContactView(FormView):
    template_name = 'agora_core/contact_form.html'
    form_class = ContactForm

    def get_form_kwargs(self):
        kwargs = super(ContactView, self).get_form_kwargs()
        kwargs.update({'request': self.request})
        return kwargs

    def get_success_url(self):
        return reverse('home')


    def form_valid(self, form):
        # This method is called when valid form data has been POSTed.
        # It should return an HttpResponse.
        form.send()
        return super(ContactView, self).form_valid(form)

