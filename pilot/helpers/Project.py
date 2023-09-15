import json
import os
import time

from fabulous.color import bold, green, yellow, cyan, white
from const.common import IGNORE_FOLDERS, STEPS
from database.models.app import App
from database.database import get_app, delete_unconnected_steps_from, delete_all_app_development_data
from helpers.ipc import IPCClient
from const.ipc import MESSAGE_TYPE
from helpers.exceptions.TokenLimitError import TokenLimitError
from utils.questionary import styled_text
from helpers.files import get_files_content, clear_directory, update_file
from helpers.cli import build_directory_tree
from helpers.agents.TechLead import TechLead
from helpers.agents.Developer import Developer
from helpers.agents.Architect import Architect
from helpers.agents.ProductOwner import ProductOwner

from database.models.development_steps import DevelopmentSteps
from database.models.file_snapshot import FileSnapshot
from database.models.files import File
from utils.files import get_parent_folder


class Project:
    def __init__(self, args, name=None, description=None, user_stories=None, user_tasks=None, architecture=None,
                 development_plan=None, current_step=None, ipc_client_instance=None):
        """
        Initialize a project.

        Args:
            args (dict): Project arguments.
            name (str, optional): Project name. Default is None.
            description (str, optional): Project description. Default is None.
            user_stories (list, optional): List of user stories. Default is None.
            user_tasks (list, optional): List of user tasks. Default is None.
            architecture (str, optional): Project architecture. Default is None.
            development_plan (str, optional): Development plan. Default is None.
            current_step (str, optional): Current step in the project. Default is None.
        """
        self.args = args
        self.llm_req_num = 0
        self.command_runs_count = 0
        self.user_inputs_count = 0
        self.checkpoints = {
            'last_user_input': None,
            'last_command_run': None,
            'last_development_step': None,
        }
        # TODO make flexible
        self.root_path = ''
        self.skip_until_dev_step = None
        self.skip_steps = None

        self.ipc_client_instance = ipc_client_instance

        # self.restore_files({dev_step_id_to_start_from})

        if current_step is not None:
            self.current_step = current_step
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        if user_stories is not None:
            self.user_stories = user_stories
        if user_tasks is not None:
            self.user_tasks = user_tasks
        if architecture is not None:
            self.architecture = architecture
        # if development_plan is not None:
        #     self.development_plan = development_plan

        print(green(bold('\n------------------ STARTING NEW PROJECT ----------------------')))
        print(f"If you wish to continue with this project in future run:")
        print(green(bold(f'python main.py app_id={args["app_id"]}')))
        print(green(bold('--------------------------------------------------------------\n')))

    def start(self):
        """
        Start the project.
        """
        self.project_manager = ProductOwner(self)
        print(json.dumps({
            "project_stage": "project_description"
        }), type='info')
        self.project_manager.get_project_description()
        print(json.dumps({
            "project_stage": "user_stories"
        }), type='info')
        self.user_stories = self.project_manager.get_user_stories()
        # self.user_tasks = self.project_manager.get_user_tasks()

        print(json.dumps({
            "project_stage": "architecture"
        }), type='info')
        self.architect = Architect(self)
        self.architecture = self.architect.get_architecture()

        self.developer = Developer(self)
        self.developer.set_up_environment();

        self.tech_lead = TechLead(self)
        self.development_plan = self.tech_lead.create_development_plan()

        # TODO move to constructor eventually
        if self.args['step'] is not None and STEPS.index(self.args['step']) < STEPS.index('coding'):
            clear_directory(self.root_path)
            delete_all_app_development_data(self.args['app_id'])
            self.skip_steps = False

        if 'skip_until_dev_step' in self.args:
            self.skip_until_dev_step = self.args['skip_until_dev_step']
            if self.args['skip_until_dev_step'] == '0':
                clear_directory(self.root_path)
                delete_all_app_development_data(self.args['app_id'])
                self.skip_steps = False
            elif self.skip_until_dev_step is not None:
                should_overwrite_files = ''
                while should_overwrite_files != 'y' or should_overwrite_files != 'n':
                    should_overwrite_files = styled_text(
                        self,
                        f'Do you want to overwrite the dev step {self.args["skip_until_dev_step"]} code with system changes? Type y/n',
                        ignore_user_input_count=True
                    )

                    if should_overwrite_files == 'n':
                        break
                    elif should_overwrite_files == 'y':
                        FileSnapshot.delete().where(FileSnapshot.app == self.app and FileSnapshot.development_step == self.skip_until_dev_step).execute()
                        self.save_files_snapshot(self.skip_until_dev_step)
                        break
        # TODO END

        self.developer = Developer(self)
        print(json.dumps({
            "project_stage": "environment_setup"
        }), type='info')
        self.developer.set_up_environment();

        print(json.dumps({
            "project_stage": "coding"
        }), type='info')
        self.developer.start_coding()

    def get_directory_tree(self, with_descriptions=False):
        """
        Get the directory tree of the project.

        Args:
            with_descriptions (bool, optional): Whether to include descriptions. Default is False.

        Returns:
            dict: The directory tree.
        """
        files = {}
        if with_descriptions and False:
            files = File.select().where(File.app_id == self.args['app_id'])
            files = {snapshot.name: snapshot for snapshot in files}
        return build_directory_tree(self.root_path + '/', ignore=IGNORE_FOLDERS, files=files, add_descriptions=False)

    def get_test_directory_tree(self):
        """
        Get the directory tree of the tests.

        Returns:
            dict: The directory tree of tests.
        """
        # TODO remove hardcoded path
        return build_directory_tree(self.root_path + '/tests', ignore=IGNORE_FOLDERS)

    def get_all_coded_files(self):
        """
        Get all coded files in the project.

        Returns:
            list: A list of coded files.
        """
        files = File.select().where(File.app_id == self.args['app_id'])

        # TODO temoprary fix to eliminate files that are not in the project
        files = [file for file in files if len(FileSnapshot.select().where(FileSnapshot.file_id == file.id)) > 0]
        # TODO END

        files = self.get_files([file.path + '/' + file.name for file in files])

        # TODO temoprary fix to eliminate files that are not in the project
        files = [file for file in files if file['content'] != '']
        # TODO END

        return files

    def get_files(self, files):
        """
        Get file contents.

        Args:
            files (list): List of file paths.

        Returns:
            list: A list of files with content.
        """
        files_with_content = []
        for file in files:
            # TODO this is a hack, fix it
            try:
                relative_path, full_path = self.get_full_file_path('', file)
                file_content = open(full_path, 'r').read()
            except:
                file_content = ''

            files_with_content.append({
                "path": file,
                "content": file_content
            })
        return files_with_content

    def save_file(self, data):
        """
        Save a file.

        Args:
            data (dict): File data.
        """
        # TODO fix this in prompts
        if 'path' not in data:
            data['path'] = ''

        if 'name' not in data:
            data['name'] = ''

        if ' ' in data['name'] or '.' not in data['name']:
            if not data['path'].startswith('./') and not data['path'].startswith('/'):
                data['path'] = './' + data['path']
            data['name'] = data['path'].rsplit('/', 1)[1]

        if '/' in data['name']:
            if data['path'] == '':
                data['path'] = data['name'].rsplit('/', 1)[0]
            data['name'] = data['name'].rsplit('/', 1)[1]
        # TODO END

        data['path'], data['full_path'] = self.get_full_file_path(data['path'], data['name'])
        update_file(data['full_path'], data['content'])

        (File.insert(app=self.app, path=data['path'], name=data['name'], full_path=data['full_path'])
            .on_conflict(
                conflict_target=[File.app, File.name, File.path],
                preserve=[],
                update={ 'name': data['name'], 'path': data['path'], 'full_path': data['full_path'] })
            .execute())

    def get_full_file_path(self, file_path, file_name):
        file_path = file_path.replace('./', '', 1)
        file_path = file_path.rsplit(file_name, 1)[0]

        if file_path.endswith('/'):
            file_path = file_path.rstrip('/')

        if file_name.startswith('/'):
            file_name = file_name[1:]

        if not file_path.startswith('/') and file_path != '':
            file_path = '/' + file_path

        if file_name != '':
            file_name = '/' + file_name

        return (file_path, self.root_path + file_path + file_name)

    def save_files_snapshot(self, development_step_id):
        files = get_files_content(self.root_path, ignore=IGNORE_FOLDERS)
        development_step, created = DevelopmentSteps.get_or_create(id=development_step_id)

        for file in files:
            print(cyan(f'Saving file {file["path"] + "/" + file["name"]}'))
            # TODO this can be optimized so we don't go to the db each time
            file_in_db, created = File.get_or_create(
                app=self.app,
                name=file['name'],
                path=file['path'],
                full_path=file['full_path'],
            )

            file_snapshot, created = FileSnapshot.get_or_create(
                app=self.app,
                development_step=development_step,
                file=file_in_db,
                defaults={'content': file.get('content', '')}
            )
            file_snapshot.content = content = file['content']
            file_snapshot.save()

    def restore_files(self, development_step_id):
        development_step = DevelopmentSteps.get(DevelopmentSteps.id == development_step_id)
        file_snapshots = FileSnapshot.select().where(FileSnapshot.development_step == development_step)

        clear_directory(self.root_path, IGNORE_FOLDERS)
        for file_snapshot in file_snapshots:
            update_file(file_snapshot.file.full_path, file_snapshot.content);

    def delete_all_steps_except_current_branch(self):
        delete_unconnected_steps_from(self.checkpoints['last_development_step'], 'previous_step')
        delete_unconnected_steps_from(self.checkpoints['last_command_run'], 'previous_step')
        delete_unconnected_steps_from(self.checkpoints['last_user_input'], 'previous_step')

    def ask_for_human_intervention(self, message, description=None, cbs={}, convo=None, is_root_task=False):
        answer = ''
        if convo is not None:
            reset_branch_id = convo.save_branch()

        while answer != 'continue':
            print(yellow(bold(message)))
            if description is not None:
                print('\n' + '-'*100 + '\n' +
                    white(bold(description)) +
                    '\n' + '-'*100 + '\n')
            try:
                answer = styled_text(
                    self,
                    'If something is wrong, tell me or type "continue" to continue.',
                )

                if answer in cbs:
                    return cbs[answer](convo)
                elif answer != '':
                    return { 'user_input': answer }
                
            except TokenLimitError as e:
                if is_root_task:
                    convo.load_branch(reset_branch_id)
                    answer = ''
                else:
                    raise e

    def log(self, text, message_type):
        if self.ipc_client_instance is None or self.ipc_client_instance.client is None:
            print(text)
        else:
            self.ipc_client_instance.send({
                'type': MESSAGE_TYPE[message_type],
                'content': str(text),
            })
            if message_type == MESSAGE_TYPE['user_input_request']:
                return self.ipc_client_instance.listen()
