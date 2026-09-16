import type {
	IAuthenticateGeneric,
	ICredentialTestRequest,
	ICredentialType,
	INodeProperties,
} from 'n8n-workflow';

export class OrdigovernanceApi implements ICredentialType {
	name = 'ordigovernanceApi';

	displayName = 'Ordigovernance Gateway API';

	documentationUrl = 'https://github.com/orditect/orditect-governance';

	properties: INodeProperties[] = [
		{
			displayName: 'Base URL',
			name: 'baseUrl',
			type: 'string',
			default: 'http://localhost:8180',
			description:
				'The ordigovernance gateway base URL (http://gateway:8180 inside the compose network)',
		},
		{
			displayName: 'Token',
			name: 'token',
			type: 'string',
			typeOptions: { password: true },
			default: '',
			description: 'The GATEWAY_AUTH_TOKEN of the gateway deployment',
		},
	];

	authenticate: IAuthenticateGeneric = {
		type: 'generic',
		properties: {
			headers: {
				Authorization: '=Bearer {{$credentials.token}}',
			},
		},
	};

	test: ICredentialTestRequest = {
		request: {
			baseURL: '={{$credentials.baseUrl}}',
			url: '/healthz',
			method: 'GET',
		},
	};
}