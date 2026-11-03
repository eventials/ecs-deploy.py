# -*- coding: utf-8 -*-
import unittest

try:
    from unittest import mock
except ImportError:  # pragma: no cover
    import mock

import boto3

import ecs_deploy

TD_ARN = 'arn:aws:ecs:us-east-1:111122223333:task-definition/my-service:7'
SSM = 'arn:aws:ssm:us-east-1:111122223333:parameter'


def described_task_definition():
    return {
        'taskDefinitionArn': TD_ARN,
        'family': 'my-service',
        'revision': 7,
        'status': 'ACTIVE',
        'registeredAt': '2026-11-03T00:00:00Z',
        'registeredBy': 'arn:aws:iam::111122223333:role/ci',
        'compatibilities': ['EC2'],
        'requiresAttributes': [{'name': 'com.amazonaws.ecs.capability.ecr-auth'}],
        'executionRoleArn': 'arn:aws:iam::111122223333:role/exec',
        'taskRoleArn': 'arn:aws:iam::111122223333:role/task',
        'networkMode': 'bridge',
        'cpu': '256',
        'memory': '512',
        'volumes': [{'name': 'data', 'host': {'sourcePath': '/data'}}],
        'placementConstraints': [{'type': 'memberOf', 'expression': 'attribute:x == y'}],
        'tags': [{'key': 'team', 'value': 'devops'}],
        'containerDefinitions': [{
            'name': 'app',
            'image': 'repo/app:old',
            'environment': [{'name': 'APP_ENV', 'value': 'production'}],
            'secrets': [{'name': 'RDBMS_URL', 'valueFrom': SSM + '/shared/rdbms/production/url'}],
        }],
    }


def make_cli(**args):
    cli = object.__new__(ecs_deploy.CLI)
    cli.credentials = {}
    cli.client = boto3.client('ecs', region_name='us-east-1')
    base = {'container_image': ['app=repo/app:new'], 'ssm_path': None,
            'ssm_container': None, 'task_role_arn': None}
    base.update(args)
    cli.args = base
    cli.task_definition = described_task_definition()
    return cli


class SsmEnvNameTest(unittest.TestCase):
    def test_converts_kebab_case(self):
        self.assertEqual(ecs_deploy.ssm_env_name('/apps/svc/production/app-jwt-secret'), 'APP_JWT_SECRET')
        self.assertEqual(ecs_deploy.ssm_env_name('/apps/svc/production/aws-account-id'), 'AWS_ACCOUNT_ID')

    def test_rejects_invalid_names(self):
        self.assertIsNone(ecs_deploy.ssm_env_name('/apps/svc/production/1-bad'))
        self.assertIsNone(ecs_deploy.ssm_env_name('/apps/svc/production/bad.name'))


class RegisterTaskDefinitionTest(unittest.TestCase):
    def test_copies_every_registrable_field_and_tags(self):
        kwargs = make_cli().client_kwargs('register_task_definition')
        for field in ('family', 'executionRoleArn', 'taskRoleArn', 'networkMode', 'cpu',
                      'memory', 'volumes', 'placementConstraints', 'tags'):
            self.assertIn(field, kwargs)
        for field in ('taskDefinitionArn', 'revision', 'status', 'registeredAt',
                      'registeredBy', 'compatibilities', 'requiresAttributes'):
            self.assertNotIn(field, kwargs)
        self.assertEqual(kwargs['containerDefinitions'][0]['image'], 'repo/app:new')

    def test_task_role_override(self):
        kwargs = make_cli(task_role_arn='arn:aws:iam::111122223333:role/other') \
            .client_kwargs('register_task_definition')
        self.assertEqual(kwargs['taskRoleArn'], 'arn:aws:iam::111122223333:role/other')

    def test_does_not_mutate_the_described_task_definition(self):
        cli = make_cli(ssm_path=['/apps/svc/production/'])
        with mock.patch.object(cli, '_ssm_parameter_names', return_value=['/apps/svc/production/new-key']):
            cli.client_kwargs('register_task_definition')
        self.assertEqual(len(cli.task_definition['containerDefinitions'][0]['secrets']), 1)
        self.assertEqual(cli.task_definition['containerDefinitions'][0]['image'], 'repo/app:old')


class SsmSecretsTest(unittest.TestCase):
    def register(self, names, **args):
        cli = make_cli(ssm_path=['/apps/svc/production/'], **args)
        with mock.patch.object(cli, '_ssm_parameter_names', return_value=names):
            return cli.client_kwargs('register_task_definition')['containerDefinitions'][0]['secrets']

    def test_adds_parameters_as_secrets(self):
        secrets = self.register(['/apps/svc/production/app-jwt-secret', '/apps/svc/production/aws-account-id'])
        self.assertIn({'name': 'APP_JWT_SECRET', 'valueFrom': SSM + '/apps/svc/production/app-jwt-secret'}, secrets)
        self.assertIn({'name': 'AWS_ACCOUNT_ID', 'valueFrom': SSM + '/apps/svc/production/aws-account-id'}, secrets)
        self.assertIn({'name': 'RDBMS_URL', 'valueFrom': SSM + '/shared/rdbms/production/url'}, secrets)

    def test_keeps_existing_secret_with_the_same_parameter(self):
        cli = make_cli(ssm_path=['/apps/svc/production/'])
        cli.task_definition['containerDefinitions'][0]['secrets'].append(
            {'name': 'APP_JWT_SECRET', 'valueFrom': SSM + '/apps/svc/production/app-jwt-secret'})
        with mock.patch.object(cli, '_ssm_parameter_names', return_value=['/apps/svc/production/app-jwt-secret']):
            secrets = cli.client_kwargs('register_task_definition')['containerDefinitions'][0]['secrets']
        self.assertEqual([s['name'] for s in secrets].count('APP_JWT_SECRET'), 1)

    def test_fails_when_name_is_a_plain_environment_variable(self):
        with self.assertRaises(SystemExit):
            self.register(['/apps/svc/production/app-env'])

    def test_fails_when_secret_points_to_another_parameter(self):
        with self.assertRaises(SystemExit):
            self.register(['/apps/svc/production/rdbms-url'])

    def test_fails_for_unknown_container(self):
        with self.assertRaises(SystemExit):
            self.register(['/apps/svc/production/x'], ssm_container='other')


if __name__ == '__main__':
    unittest.main()
