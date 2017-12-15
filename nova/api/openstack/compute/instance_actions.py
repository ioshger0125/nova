# Copyright 2013 Rackspace Hosting
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from webob import exc

from oslo_utils import timeutils

from nova.api.openstack import api_version_request
from nova.api.openstack import common
from nova.api.openstack.compute.schemas \
    import instance_actions as schema_instance_actions
from nova.api.openstack.compute.views \
    import instance_actions as instance_actions_view
from nova.api.openstack import extensions
from nova.api.openstack import wsgi
from nova.api import validation
from nova import compute
from nova import exception
from nova.i18n import _
from nova.policies import instance_actions as ia_policies
from nova import utils


ACTION_KEYS = ['action', 'instance_uuid', 'request_id', 'user_id',
               'project_id', 'start_time', 'message']
ACTION_KEYS_V258 = ['action', 'instance_uuid', 'request_id', 'user_id',
                    'project_id', 'start_time', 'message', 'updated_at']
EVENT_KEYS = ['event', 'start_time', 'finish_time', 'result', 'traceback']


class InstanceActionsController(wsgi.Controller):
    _view_builder_class = instance_actions_view.ViewBuilder

    def __init__(self):
        super(InstanceActionsController, self).__init__()
        self.compute_api = compute.API()
        self.action_api = compute.InstanceActionAPI()

    def _format_action(self, action_raw, action_keys):
        action = {}
        for key in action_keys:
            action[key] = action_raw.get(key)
        return action

    def _format_event(self, event_raw, show_traceback=False):
        event = {}
        for key in EVENT_KEYS:
            # By default, non-admins are not allowed to see traceback details.
            if key == 'traceback' and not show_traceback:
                continue
            event[key] = event_raw.get(key)
        return event

    @wsgi.Controller.api_version("2.1", "2.20")
    def _get_instance(self, req, context, server_id):
        return common.get_instance(self.compute_api, context, server_id)

    @wsgi.Controller.api_version("2.21")  # noqa
    def _get_instance(self, req, context, server_id):
        with utils.temporary_mutation(context, read_deleted='yes'):
            return common.get_instance(self.compute_api, context, server_id)

    @wsgi.Controller.api_version("2.1", "2.57")
    @extensions.expected_errors(404)
    def index(self, req, server_id):
        """Returns the list of actions recorded for a given instance."""
        context = req.environ["nova.context"]
        instance = self._get_instance(req, context, server_id)
        context.can(ia_policies.BASE_POLICY_NAME, instance)
        actions_raw = self.action_api.actions_get(context, instance)
        actions = [self._format_action(action, ACTION_KEYS)
                   for action in actions_raw]
        return {'instanceActions': actions}

    @wsgi.Controller.api_version("2.58")  # noqa
    @extensions.expected_errors((400, 404))
    @validation.query_schema(schema_instance_actions.list_query_params_v258,
                             "2.58")
    def index(self, req, server_id):
        """Returns the list of actions recorded for a given instance."""
        context = req.environ["nova.context"]
        instance = self._get_instance(req, context, server_id)
        context.can(ia_policies.BASE_POLICY_NAME, instance)
        search_opts = {}
        search_opts.update(req.GET)
        if 'changes-since' in search_opts:
            search_opts['changes-since'] = timeutils.parse_isotime(
                search_opts['changes-since'])

        limit, marker = common.get_limit_and_marker(req)
        try:
            actions_raw = self.action_api.actions_get(context, instance,
                                                      limit=limit,
                                                      marker=marker,
                                                      filters=search_opts)
        except exception.MarkerNotFound as e:
            raise exc.HTTPBadRequest(explanation=e.format_message())
        actions = [self._format_action(action, ACTION_KEYS_V258)
                   for action in actions_raw]
        actions_dict = {'instanceActions': actions}
        actions_links = self._view_builder.get_links(req, server_id, actions)
        if actions_links:
            actions_dict['links'] = actions_links
        return actions_dict

    @extensions.expected_errors(404)
    def show(self, req, server_id, id):
        """Return data about the given instance action."""
        context = req.environ['nova.context']
        instance = self._get_instance(req, context, server_id)
        context.can(ia_policies.BASE_POLICY_NAME, instance)
        action = self.action_api.action_get_by_request_id(context, instance,
                                                          id)
        if action is None:
            msg = _("Action %s not found") % id
            raise exc.HTTPNotFound(explanation=msg)

        action_id = action['id']
        if api_version_request.is_supported(req, min_version="2.58"):
            action = self._format_action(action, ACTION_KEYS_V258)
        else:
            action = self._format_action(action, ACTION_KEYS)
        # Prior to microversion 2.51, events would only be returned in the
        # response for admins by default policy rules. Starting in
        # microversion 2.51, events are returned for admin_or_owner (of the
        # instance) but the "traceback" field is only shown for admin users
        # by default.
        show_events = False
        show_traceback = False
        if context.can(ia_policies.POLICY_ROOT % 'events', fatal=False):
            # For all microversions, the user can see all event details
            # including the traceback.
            show_events = show_traceback = True
        elif api_version_request.is_supported(req, '2.51'):
            # The user is not able to see all event details, but they can at
            # least see the non-traceback event details.
            show_events = True

        if show_events:
            events_raw = self.action_api.action_events_get(context, instance,
                                                           action_id)
            action['events'] = [self._format_event(evt, show_traceback)
                                for evt in events_raw]
        return {'instanceAction': action}
