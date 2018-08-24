#!/usr/bin/env python
# coding: utf-8

"""Ansible dynamic inventory for GLPI REST API."""

import os
import sys
import re
import copy
import argparse
import yaml
import yamlloader
import requests
import json
from lib.glpi import GLPI, GLPIError
from pprint import pprint


# HTTP requests debugging
#import logging
#requests_logger = logging.getLogger('urllib3')
#requests_handler = logging.StreamHandler()
#requests_handler.setFormatter(logging.Formatter())
#requests_logger.addHandler(requests_handler)
#requests_logger.setLevel('DEBUG')
##import http.client as http_client
##http_client.HTTPConnection.debuglevel = 1


SEARCH_PARAMETERS = ('itemtype', 'criteria', 'metacriteria', 'fields')
PARAMETERS = ('hostname',
              'hostvars',
              'vars',
              'retrieve')

class GLPIInventoryError(Exception):
    """Exception for this program (catched in `main`)."""
    pass

def main():
    """Main function."""
    # args is returned as a dictionnary and contains the arguments with their
    # values. Groups configuration is also loaded (args['groups']) from groups
    # configuration file indicated with --groups-config option.
    args = init_cli()
    try:
        # Connect to GLPI API.
        glpi = GLPI(url=args['glpi_url'],
                    apptoken=args['glpi_apptoken'],
                    usertoken=args['glpi_usertoken'])

        # Retrieve complete inventory from GLPI based on groups configuration.
        inventory = generate_inventory(glpi, prepare_config(args['groups']))

        # If --host option is used, return variables of the host generated by
        # the inventory.
        if args['host']:
            inventory = inventory['hostvars'][args['host']]

        # Print inventory as JSON as required.
        print(json.dumps(inventory))
    except GLPIError as err:
        print('unable to connect to GLPI: {:s}'.format(str(err)))
        sys.exit(1)
    except GLPIInventoryError as err:
        sys.stderr.write('error: {:s}\n'.format(str(err)))
        sys.exit(1)
    sys.exit(0)

#
# CLI
#
def init_cli():
    """Initialize the CLI.

    Configuration parameters are managed by this CLI but are automatically
    retrieved from environment variables (which will always have precedence
    against command input when defined).
    """
    parser = argparse.ArgumentParser(description='GLPI Inventory Module')

    # Debugging
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug output')

    # GLPI connection parameters.
    parser.add_argument('--glpi-url',
                        default=os.environ.get('GLPI_API_URL'),
                        help='URL for connecting to GLPI (default from '
                             '$GLPI_API_URL)')
    parser.add_argument('--glpi-usertoken',
                        default=os.environ.get('GLPI_API_USERTOKEN'),
                        help='User token for connecting to GLPI (default from '
                             '$GLPI_API_USERTOKEN)')
    parser.add_argument('--glpi-apptoken',
                        default=os.environ.get('GLPI_API_APPTOKEN'),
                        help='Password for connecting to GLPI (default from '
                             '$GLPI_API_APPTOKEN)')

    # GLPI groups configuration.
    # If $GLPI_GROUPS_FILE is not defined, the file 'glpi-api.yml' beside this
    # file is used.
    config_path = os.path.join(os.path.dirname(__file__), 'glpi-api.yml')
    parser.add_argument('--groups-config',
                        default=os.environ.get('GLPI_GROUPS_FILE', config_path),
                        metavar='GROUPS_CONFIG_PATH',
                        help='Groups configuration (default from glpi-api.yml '
                             'beside this file)')

    # Ansible required options.
    ansible_group = parser.add_mutually_exclusive_group(required=True)
    ansible_group.add_argument('--list', action='store_true',
                               help='List active servers')
    ansible_group.add_argument('--host',
                              help='List details about the specific host')

    # Retrieve arguments as dict (for being able to edit it).
    args = vars(parser.parse_args())

    # Ensure the parameters for connecting to GLPI are defined. It is done
    # manually as the default values of these parameters are set from the
    # environment variables.
    missing_parameters = [arg
                          for arg in ('glpi_url', 'glpi_usertoken', 'glpi_apptoken')
                          if args[arg] is None]
    if missing_parameters:
        args_str = ', '.join('--{:s}'.format(arg.replace('_', '-'))
                             for arg in missing_parameters)
        parser.error('the following arguments are required: {:s}'.format(args_str))

    # Add groups configuration to args from configuration file.
    try:
        with open(args['groups_config']) as fhandler:
            args['groups'] = yaml.load(fhandler, Loader=yamlloader.ordereddict.CLoader)
    except (IOError, yaml.scanner.ScannerError) as err:
        parser.error('unable to load configuration file ({:s}):\n{:s}'
                     .format(args['groups_config'], str(err)))

    return args

#
# Inventory
#
def prepare_config(groups_conf):
    """Recursive function that generate the arborescence of the groups
    from configuration."""
    def parse_group(group, groups):
        group_conf = groups_conf[group]
        if not isinstance(group_conf, dict):
            raise GLPIInventoryError("group '{:s}' is not a valid dictionary"
                                     .format(group))

        # Generate config grom group configuraiton.
        config = {param: group_conf.pop(param)
                  for param in PARAMETERS
                  if param in group_conf}
        search_params = {param: group_conf.pop(param)
                         for param in SEARCH_PARAMETERS
                         if param in group_conf}
        # Replace 'fields' parameter by 'forcedisplay'.
        search_params['forcedisplay'] = search_params.pop('fields', [])
        config.setdefault('search_params', search_params)

        # Get children groups.
        children = group_conf.pop('children', [])

        if group_conf:
            raise GLPIInventoryError("group '{:s}' has invalid keys '{:s}'"
                                     .format(group, ', '.join(group_conf)))

        for child in children:
            try:
                (config.setdefault('children', {})
                       .setdefault(child, parse_group(child, groups_conf[child])))
            except KeyError:
                raise GLPIInventoryError("group '{:s}' is not defined".format(child))
            groups_name.remove(child)
        return config

    groups = {}
    groups_name = list(groups_conf.keys())
    while groups_name:
        group = groups_name.pop(0)
        groups.setdefault(group, parse_group(group, groups_name))
    return groups

def generate_inventory(glpi, groups):
    """Generate inventory from `groups` configuration."""
    inventory = {}
    hostvars = {}
    for group, group_conf in groups.items():
        parse_group(glpi, group, group_conf.copy(), {}, inventory, hostvars, 0)
#    pprint(inventory)
#    pprint(hostvars)
#    inventory.setdefault('_meta', {'hostvars': hostvars})
    return inventory

def parse_group(glpi, group, group_conf, parent_conf, inventory, hostvars, level):
    #print(group)
    children = group_conf.pop('children', {})
    retrieve = group_conf.get('retrieve', False)
    group_conf = dict(group_conf)
    group_conf['search_params'] = merge_search_params(
                                    parent_conf.get('search_params', {}),
                                    group_conf.get('search_params', {}))
    group_conf['hostname'] = group_conf.get('hostname',
                                            parent_conf.get('hostname', None))
    group_conf['hostvars'] = {**group_conf.get('hostvars', {}),
                              **parent_conf.get('hostvars', {})}
    group_conf['vars'] = {**group_conf.get('vars', {}),
                          **parent_conf.get('vars', {})}

#    search_params = merge_search_params(parent_conf.get('search_params', {}),
#                                        group_conf.get('search_params', {}))
#    hostname = group_conf.get('hostname', parent_conf.get('hostname', None))
#    hostvars_ = {**group_conf.get('hostvars', {}),
#                 **parent_conf.get('hostvars', {})}
#    vars_ = {**group_conf.get('vars', {}), **parent_conf.get('vars', {})}
    #pprint(search_params)

    # Data are retrieved when there is no children or when 'retrieve'
    # parameter is set.
    retrieve = True if not children else retrieve
    if retrieve:
        if 'itemtype' not in group_conf['search_params']:
            raise GLPIInventoryError(
                "group '{:s}' has no itemtype defined when calling API"
                .format(group))

        data = glpi.search(**group_conf['search_params'], range='0-9999')
        hosts = [replace_fields_values(group_conf['hostname'], entry).lower()
                 for entry in data]
        group_inv = {'hosts': hosts, 'vars': group_conf['vars'], 'children': list(children.keys())}
        inventory.setdefault(group, group_inv)

        hostvars_inv = {
            'glpi': {param: replace_fields_values(value, entry)}
            for entry in data
            for param, value in group_conf['hostvars'].items()}
        hostvars.update({host: hostvars_inv for host in hosts})

    for child, child_group_conf in children.items():
        parse_group(glpi,
                    child,
                    child_group_conf,
                    # need to force copy of parent otherwise search parameters
                    # cummulate
                    copy.deepcopy(group_conf),
                    inventory,
                    hostvars,
                    level + 1)

def replace_fields_values(value, data):
    """Replace all occurences starting by a dollar and followed by a
    number with the corresponding field index in the data."""
    value = str(value)
    for field_idx in re.findall(r'\$(\d*)', value):
        if data[field_idx] is None:
            continue
        value = re.sub(r'\${}'.format(field_idx), data[field_idx], value)
    return value

def merge_search_params(parent_search_params, search_params):
    for param, value in search_params.items():
        #print(param, value)
        if not parent_search_params.get(param, None):
            parent_search_params[param] = value
        else:
            parent_search_params[param].extend(value)
    return parent_search_params

#        children = group_conf.pop('children', {})
#
#        # Data are retrieved when there is no children or when 'retrieve'
#        # parameter is set.
#        retrieve = True if children is None else group_conf.pop('retrieve', False)
#        if retrieve:
#            search_params = group_conf.pop('search_params')
#            #itemtype = group_conf.pop('itemtype')
#            if 'itemtype' not in search_params:
#                raise GLPIInventoryError(
#                    "group '{:s}' has no itemtype defined when calling API"
#                    .format(group))
#
#            data = glpi.search(**search_params, range='0-9999')
#            hosts = [replace_fields_values(group_conf['hostname'], entry).lower()
#                     for entry in data]
#            group_inv = {'hosts': hosts, 'vars': group_conf.pop('vars', {})}
#            inventory.setdefault(group, group_inv)
#
#            hostvars_inv = {
#                'glpi': {param: replace_fields_values(value, entry)}
#                for entry in data
#                for param, value in
#                    group_conf.pop('hostvars', {}).items()}
#            hostvars.update({host: hostvars_inv for host in hosts})
#
#        inventory.update(generate_inventory(glpi, children))
#

#    inventory = {}
#    hostvars = {}
#    for group_name, group_config in groups.items():
#        children = group_config.pop('children', None)
#        if children is None:
#            # search from params
#            pass
#    return {}


# API utils.
#
#def api_connect():
#    """Set `args.sessiontoken`."""
#    try:
#        response = requests.get(
#            url=os.path.join(args['api_url'], 'initSession'),
#            headers={
#                'Content-Type': 'application/json',
#                'Authorization': 'user_token {:s}'.format(args['api_usertoken']),
#                'App-Token': args['api_apptoken']
#            }
#        )
#        args['api_sessiontoken'] = response.json()['session_token']
#    # Probably too large ...
#    except Exception as err:
#        raise GLPIInventoryError('unable to connect to the API: {}'
#                                 .format(str(err)))
#
#def api_search(itemtype, fields, criteria=None, metacriteria=None):
#    """Search items in GLPI API."""
#    # Function for formating list parameters.
#    def format_list(param, data):
#        return {'{:s}[{:d}]'.format(param, index): value
#                for index, value in enumerate(data)}
#    # Function for formatting dict parameters.
#    def format_dict(param, data):
#        return {'{:s}[{:d}][{:s}]'.format(param, index, key): value
#                for index, criterium in enumerate(data)
#                for key, value in criterium.items()}
#
#    # Force retrieving of all items.
#    http_params = { 'range': '0-9999' }
#
#    # Manage fields, criteria and metacriteria input.
#    http_params.update(format_dict(param='criteria', data=criteria or []))
#    http_params.update(format_dict(param='metacriteria', data=metacriteria or []))
#    http_params.update(format_list(param='forcedisplay', data=fields))
#
#    # Execute request and return JSON.
#    response = requests.get(
#        url=os.path.join(args['api_url'], 'search', itemtype),
#        headers={
#            'Content-Type': 'application/json',
#            'Session-Token': args['api_sessiontoken'],
#            'App-Token': args['api_apptoken']
#        },
#        params=http_params
#    )
#    if response.status_code != 200:
#        raise GLPIInventoryError('invalid HTTP code {:d}!'
#                                 .format(response.status_code))
#
#    return response.json().get('data', [])
#    #result = response.json()
#    #return result.get('data', [])

#
# Inventory functions.
#
#PARAMS = ('itemtype', 'fields', 'criteria', 'metacriteria',
#          'hostname', 'hostvars', 'vars', 'retrieve')

#def replace_fields_values(value, data):
#    """Replace all occurences starting by a dollar and followed by a
#    number with the corresponding field index in the data."""
#    value = str(value)
#    for field_idx in re.findall(r'\$(\d*)', value):
#        if data[field_idx] is None:
#            continue
#        value = re.sub(r'\${}'.format(field_idx), data[field_idx], value)
#    return value

#def get_inventory(groups_conf):
#    # Initialize global inventory.
#    inventory = {'_meta': {'hostvars': {}}}
#    hostvars = {}
#
#    config = prepare_config(groups_conf)
#    for group, group_conf in config.items():
##        print(group)
##        pprint(group_conf)
##        retrieve, req_params = get_request_params(group_conf)
##        print(retrieve)
##        pprint(req_params)
#        gen_inventory(group, group_conf, {}, inventory, hostvars)
#
#def merge_params(params, new_params):
#    for param, value in new_params.items():
#        if param not in params:
#            params.setdefault(param, value)
#        elif isinstance(value, dict):
#            merge_params(params[param], value)
#        elif isinstance(value, (list, tuple)):
#            params[param].extend(value)
#    return params
#
#def gen_inventory(group, group_conf, req_params, inventory, hostvars):
#    """
#    config: arborescence of groups with search parameters.
#    inventory: global inventory (provisioned by this function)
#    hostvars: global hostvars (provisioned by this function)
#    """
#    print(group)
#    req_params = merge_params(req_params, group_conf.pop('_params', {}))
#    #print(req_params)
#    children = list(group_conf.keys())
#    #print(children)
#    #print(group_conf.keys())
#
#    for child_group, child_group_conf in group_conf.items():
#        gen_inventory(child_group, child_group_conf, req_params, inventory, hostvars)
#
##    for group, group_conf in config.items():
##        merge_params(req_params, group_conf.pop('_params', {}))
##        print(group)
##        pprint(req_params)
##        itemtype = req_params.pop('itemtype', None)
##        children = list(group_conf.keys())
##        retrieve = req_params.pop('retrieve', False)
##
###        if not children or retrieve:
###            if itemtype is None:
###                raise GLPIInventoryError('({:s}) group has no item type'.format(group))
###            data = glpi.search(itemtype, **req_params)
###            pprint(data)
##
##        if children:
##            gen_inventory(group_conf, inventory, hostvars, req_params)
##
###        print(req_params)
###        print(group_conf.keys())
###        #pprint(group_conf)
###        retrieve, req_params = get_request_params(group_conf)
###        print(retrieve)
###        pprint(req_params)
###        pprint(group_conf)
####    return inventory
#
#def prepare_config(groups_conf):
#    """Recursive funcion that generate the arborescence of groups and for each
#    group it prefix request parameters with underscores.
#    """
#    def parse_group(group, groups):
#        group_conf = groups_conf[group]
#        if not isinstance(group_conf, dict):
#            raise GLPIInventoryError("group '{:s}' is not a valid dictionary"
#                                     .format(group))
#
#        # prefix parameters keys, except 'children', with an underscore
#        # for lisibility.
#        config = {}
#        for param in PARAMS:
#            if param in group_conf:
#                config.setdefault('_params', {}).setdefault(param, group_conf.pop(param))
#
#        children = group_conf.pop('children', [])
#
#        if group_conf:
#            raise GLPIInventoryError("group '{:s}' has invalid keys '{:s}'"
#                                     .format(group, ', '.join(group_conf)))
#        for child in children:
#            try:
#                config.setdefault(child, parse_group(child, groups_conf[child]))
#            except KeyError:
#                raise GLPIInventoryError("group '{:s}' is not defined".format(child))
#            groups_name.remove(child)
#        return config
#
#    groups = {}
#    groups_name = list(groups_conf.keys())
#    while groups_name:
#        group = groups_name.pop(0)
#        #groups.setdefault(group, parse_group(group, groups_conf[group]))
#        groups.setdefault(group, parse_group(group, groups_name))
#    return groups

#def generate_inventory(groups_config):
#    """
#    """
#    def get_child(group_name, group_config):
#        return { group_name: 'test' }
#
#    inventory = {}
#    hostvars = {}
#
#    for group_name in list(groups_config.keys()):
#        group_conf = groups_config.pop(group_name)
#        children = group_conf.pop('children', [])
#        retrieve = group_conf.pop('retrieve', False)
#        if not children or retrieve:
#            hostvars, hosts = get_data(group_conf)
#
#        if not children:
#            continue
#
#        for child in children:
#            inventory.update(**get_child(child, groups_config[child]))
##        inventory.setdefault()
##        group = {
##            'vars': group_conf.pop('vars', {}),
##            'children': children
##        }
##        print(group)
#
#    return inventory
#

#    for group, group_conf in groups_config.copy().items():
#        children = group_conf.pop('children', [])
#        if not children:
#            continue
#        groups_config.pop()
#        print(group, children)
#        for child in children:
#            print(child)
#            get_inventory(groups_config.pop(child))

if __name__ == '__main__':
    main()