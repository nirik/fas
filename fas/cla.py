# -*- coding: utf-8 -*-
#
# Copyright © 2008  Ricky Zhou All rights reserved.
# Copyright © 2008 Red Hat, Inc. All rights reserved.
#
# This copyrighted material is made available to anyone wishing to use, modify,
# copy, or redistribute it subject to the terms and conditions of the GNU
# General Public License v.2.  This program is distributed in the hope that it
# will be useful, but WITHOUT ANY WARRANTY expressed or implied, including the
# implied warranties of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.  You should have
# received a copy of the GNU General Public License along with this program;
# if not, write to the Free Software Foundation, Inc., 51 Franklin Street,
# Fifth Floor, Boston, MA 02110-1301, USA. Any Red Hat trademarks that are
# incorporated in the source code or documentation are not subject to the GNU
# General Public License and may only be used or replicated with the express
# permission of Red Hat, Inc.
#
# Author(s): Ricky Zhou <ricky@fedoraproject.org>
#            Mike McGrath <mmcgrath@redhat.com>
#
import turbogears
from turbogears import controllers, expose, paginate, identity, redirect, widgets, validate, validators, error_handler
from turbogears.database import session

import cherrypy

from sqlalchemy.exceptions import SQLError

from datetime import datetime
import re
import GeoIP
import turbomail
from genshi.template.plugin import TextTemplateEnginePlugin

from fedora.tg.util import request_format

from fas.model import People
from fas.model import Log
from fas.auth import *
# import * isn't good practice.  Remove when we have all the improts in the
# line below:
from fas.auth import isAdmin
import fas

class CLA(controllers.Controller):

    # Group name for people having signed the CLA
    CLAGROUPNAME = config.get('cla_fedora_group')
    # Meta group for everyone who has satisfied the requirements of the CLA
    # (By signing or having a corporate signatue or, etc)
    CLAMETAGROUPNAME = config.get('cla_done_group')

    # Values legal in phone numbers
    PHONEDIGITS = ('0','1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '+',
            '-', ')' ,'(', ' ')

    def __init__(self):
        '''Create a CLA Controller.'''

    @identity.require(turbogears.identity.not_anonymous())
    @expose(template="fas.templates.cla.index")
    def index(self):
        '''Display the CLAs (and accept/do not accept buttons)'''
        username = turbogears.identity.current.user_name
        person = People.by_username(username)
        try:
            code_len = len(person.country_code)
        except TypeError:
            code_len = 0
        print "%s - %s" % (person.country_code, code_len)
        if not person.telephone or not person.postal_address or code_len != 2 or person.country_code=='  ':
            turbogears.flash('A valid postal Address, country and telephone number are required to complete the CLA.  Please fill them out below.')
            turbogears.redirect('/user/edit/%s' % username)
        cla = CLADone(person)
        return dict(cla=cla, person=person, date=datetime.utcnow().ctime())

    def _cla_dependent(self, group):
        '''
        Check whether a group has the cla in its prerequisite chain.

        Arguments:
        :group: group to check

        Returns: True if the group requires the cla_group_name otherwise
        '''
        if group.name in (self.CLAGROUPNAME, self.CLAMETAGROUPNAME):
            return True
        if group.prerequisite_id:
            return self._cla_dependent(group.prerequisite)
        return False

    def jsonRequest(self):
        return 'tg_format' in cherrypy.request.params and \
                cherrypy.request.params['tg_format'] == 'json'

    @expose(template="fas.templates.error")
    def error(self, tg_errors=None):
        '''Show a friendly error message'''
        if not tg_errors:
            turbogears.redirect('/')
        return dict(tg_errors=tg_errors)

    @identity.require(turbogears.identity.not_anonymous())
    @error_handler(error)
    @expose(template="genshi-text:fas.templates.cla.cla", format="text", content_type='text/plain; charset=utf-8')
    def text(self, type=None):
        '''View CLA as text'''
        username = turbogears.identity.current.user_name
        person = People.by_username(username)
        return dict(person=person, date=datetime.utcnow().ctime())

    @identity.require(turbogears.identity.not_anonymous())
    @error_handler(error)
    @expose(template="genshi-text:fas.templates.cla.cla", format="text", content_type='text/plain; charset=utf-8')
    def download(self, type=None):
        '''Download CLA'''
        username = turbogears.identity.current.user_name
        person = People.by_username(username)
        return dict(person=person, date=datetime.utcnow().ctime())

    @identity.require(turbogears.identity.not_anonymous())
    @error_handler(error)
    @expose(template="fas.templates.user.view", allow_json=True)
    def reject(self, personName):
        '''Reject a user's CLA.

        This method will remove a user from the CLA group and any other groups
        that they are in that require the CLA.  It is used when a person has
        to fulfill some more legal requirements before having a valid CLA.

        Arguments
        :personName: Name of the person to reject.
        '''
        exc = None
        user = People.by_username(turbogears.identity.current.user_name)
        if not isAdmin(user):
            # Only admins can use this
            turbogears.flash(_('You are not allowed to reject CLAs.'))
            exc = 'NotAuthorized'
        else:
            # Unapprove the cla and all dependent groups
            person = People.by_username(personName)
            for role in person.approved_roles:
                if self._cla_dependent(role.group):
                    role.role_status = 'unapproved'
            try:
                session.flush()
            except SQLError, e:
                turbogears.flash(_('Error removing cla and dependent groups' \
                        ' for %(person)s\n Error was: %(error)s') %
                        {'person': personName, 'error': str(e)})
                exc = 'sqlalchemy.SQLError'

        if not exc:
            # Send a message that the ICLA has been revoked
            dt = datetime.utcnow()
            Log(author_id=user.id, description='Revoked %s CLA' % person.username, changetime=dt)
            message = turbomail.Message(config.get('accounts_email'), person.email, 'Fedora ICLA Revoked')
            message.plain = '''
Hello %(human_name)s,

We're sorry to bother you but we had to reject your CLA for now because
information you provided has been deemed incorrect.  Common causes of this
are using a name, address/country, or phone number that isn't accurate [1]_.  
If you could edit your account [2]_ to fix any of these problems and resubmit
the CLA we would appreciate it.

.. [1]: Why does it matter that we have your real name, address and phone
        number?   It's because the CLA is a legal document and should we ever
        need to contact you about one of your contributions (as an example,
        because someone contacts *us* claiming that it was really they who
        own the copyright to the contribution) we might need to contact you
        for more information about what's going on.

.. [2]: Edit your account by logging in at this URL:
        https://admin.fedoraproject.org/accounts/user/edit/%(username)s

If you have questions about what specifically might be the problem with your
account, please contact us at accounts@fedoraproject.org.

Thanks!
    ''' % {'username': person.username,
    'human_name': person.human_name, }
            turbomail.enqueue(message)

            # Yay, sweet success!
            turbogears.flash(_('CLA Successfully Removed.'))
        # and now we're done
        if request_format() == 'json':
            returnVal = {}
            if exc:
                returnVal['exc'] = exc
            return returnVal
        else:
            turbogears.redirect('/user/view/%s' % personName)

    @identity.require(turbogears.identity.not_anonymous())
    @error_handler(error)
    @expose(template="fas.templates.cla.index")
    def send(self, human_name, telephone, postal_address, country_code, confirm=False, agree=False):
        '''Send CLA'''
        username = turbogears.identity.current.user_name
        person = People.by_username(username)
        if CLADone(person):
            turbogears.flash(_('You have already completed the CLA.'))
            turbogears.redirect('/cla/')
            return dict()
        if not agree:
            turbogears.flash(_("You have not completed the CLA."))
            turbogears.redirect('/user/view/%s' % person.username)
        if not confirm:
            turbogears.flash(_('You must confirm that your personal information is accurate.'))
            turbogears.redirect('/cla/')

        # Compare old information to new to see if any changes have been made
        if human_name and person.human_name != human_name:
            person.human_name = human_name
        if telephone and person.telephone != telephone:
            person.telephone = telephone
        if postal_address and person.postal_address != postal_address:
            person.postal_address = postal_address
        if country_code and person.country_code != country_code:
            person.country_code = country_code
        # Save it to the database
        try:
            session.flush()
        except Exception, e:
            turbogears.flash(_("Your updated information could not be saved."))
            turbogears.redirect('/cla/')
            return dict()
        
        # Heuristics to detect bad data
        if not person.telephone or \
                not person.postal_address or \
                not person.human_name or \
                not person.country_code:
            turbogears.flash(_('To complete the CLA, we must have your name, telephone number, postal address, and country.  Please ensure they have been filled out.'))
            turbogears.redirect('/cla/')

        if person.country_code not in GeoIP.country_codes:
            turbogears.flash(_('To complete the CLA, a valid country code must be specified.  Please select one now.'))
            turbogears.redirect('/cla/')
        if [True for char in person.telephone if char not in self.PHONEDIGITS]:
            turbogears.flash(_('Telephone numbers can only consist of numbers, "-", "+", "(", ")", or " ".  Please reenter using only those characters.'))
            turbogears.redirect('/cla/')
        if not [True for char in person.postal_address if char.isspace()]:
            # Error if the postal address is only one word
            turbogears.flash(_('Can the postal system really deliver to that address?'))
            turbogears.redirect('/cla/')

        group = Groups.by_name(self.CLAGROUPNAME)
        try:
            # Everything is correct.
            person.apply(group, person) # Apply for the new group
            session.flush()
        except fas.ApplyError, e:
            # This just means the user already is a member (probably
            # unapproved) of this group
            pass
        except Exception, e:
            turbogears.flash(_("You could not be added to the '%s' group.") % group.name)
            turbogears.redirect('/cla/')
            return dict()

        try:
            # Everything is correct.
            person.sponsor(group, person) # Sponsor!
            session.flush()
        except fas.SponsorError:
            turbogears.flash(_("You are already a part of the '%s' group.") % group.name)
            turbogears.redirect('/cla/')
        except:
            turbogears.flash(_("You could not be added to the '%s' group.") % group.name)
            turbogears.redirect('/cla/')

        dt = datetime.utcnow()
        Log(author_id=person.id, description='Completed CLA', changetime=dt)
        message = turbomail.Message(config.get('accounts_email'), config.get('legal_cla_email'), 'Fedora ICLA completed')
        message.plain = '''
Fedora user %(username)s has completed an ICLA (below).
Username: %(username)s
Email: %(email)s
Date: %(date)s

If you need to revoke it, please visit this link:
    https://admin.fedoraproject.org/accounts/cla/reject/%(username)s

=== CLA ===

''' % {'username': person.username,
'human_name': person.human_name,
'email': person.email,
'postal_address': person.postal_address,
'country_code': person.country_code,
'telephone': person.telephone,
'facsimile': person.facsimile,
'date': dt.ctime(),}
        # Sigh..  if only there were a nicer way.
        plugin = TextTemplateEnginePlugin()
        message.plain += plugin.render(template='fas.templates.cla.cla', info=dict(person=person), format='text')
        turbomail.enqueue(message)
        turbogears.flash(_("You have successfully completed the CLA.  You are now in the '%s' group.") % group.name)
        turbogears.redirect('/user/view/%s' % person.username)
        return dict()
