from __future__ import print_function, unicode_literals
import builtins
import json
import asyncio

import sys
import traceback
from dotenv import load_dotenv
load_dotenv()
from termcolor import colored
from helpers.ipc import IPCClient
from const.ipc import MESSAGE_TYPE
from utils.utils import json_serial
from fabulous.color import red

from helpers.Project import Project
from utils.arguments import get_arguments
from utils.exit import exit_gpt_pilot
from logger.logger import logger
from database.database import database_exists, create_database, tables_exist, create_tables, get_created_apps_with_steps


def init():
    # Check if the "euclid" database exists, if not, create it
    if not database_exists():
        create_database()

    # Check if the tables exist, if not, create them
    if not tables_exist():
        create_tables()

    arguments = get_arguments()

    logger.info(f"Starting with args: {arguments}")

    return arguments

def get_custom_print(args):
    built_in_print = builtins.print

    def print_to_external_process(*args, **kwargs):
        message = args[0]

        if 'type' not in kwargs:
            kwargs['type'] = 'verbose'
        elif kwargs['type'] == MESSAGE_TYPE['local']:
            local_print(*args, **kwargs)
            return

        message_with_type = f"[{kwargs['type']}] {message}"  # Include the type as a prefix
        ipc_client_instance.send({
            'type': MESSAGE_TYPE[kwargs['type']],
            'content': message_with_type,
        })
        if kwargs['type'] == MESSAGE_TYPE['user_input_request']:
            return ipc_client_instance.listen()

    def local_print(*args, **kwargs):
        message = " ".join(map(str, args))
        if 'type' in kwargs:
            if kwargs['type'] == MESSAGE_TYPE['info']:
                return
            del kwargs['type']

        built_in_print(message, **kwargs)

    ipc_client_instance = None
    if '--external-log-process-port' in args:
        ipc_client_instance = IPCClient(args['--external-log-process-port'])
        return print_to_external_process, ipc_client_instance
    else:
        return local_print, ipc_client_instance

if __name__ == "__main__":
    try:
        args = init()
        custom_print, ipc_client_instance = get_custom_print(args)
        if '--get-created-apps-with-steps' in args:
            print({ 'db_data': get_created_apps_with_steps() }, type='info')
        else:
            if ipc_client_instance:
              asyncio.get_event_loop().run_until_complete(ipc_client_instance.connect())
            project = Project(args, ipc_client_instance=ipc_client_instance)
            project.start()
    except KeyboardInterrupt:
        exit_gpt_pilot()
    except Exception as e:
        print(red(e))
        traceback.print_exc()
        print(red('--------------------------------------------------'))
        exit_gpt_pilot()
    finally:
        sys.exit(0)