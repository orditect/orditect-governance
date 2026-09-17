import {
	NodeConnectionTypes,
	type INodeType,
	type INodeTypeDescription,
	type ISupplyDataFunctions,
	type SupplyData,
} from 'n8n-workflow';

import { TrackedChatModel } from './TrackedChatModel';

export class OrdigovernanceChatModel implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Ordigovernance Chat Model',
		name: 'ordigovernanceChatModel',
		icon: 'file:ordigovernance.svg',
		group: ['transform'],
		version: 1,
		description:
			'Chat model governed by the ordigovernance gateway: every call is audited, budgeted and idempotent',
		defaults: {
			name: 'Ordigovernance Chat Model',
		},
		codex: {
			categories: ['AI'],
			subcategories: {
				AI: ['Language Models', 'Root Nodes'],
			},
			resources: {
				primaryDocumentation: [
					{
						url: 'https://github.com/orditect/orditect-governance',
					},
				],
			},
		},
		inputs: [],
		outputs: [NodeConnectionTypes.AiLanguageModel],
		credentials: [
			{
				name: 'ordigovernanceApi',
				required: true,
			},
		],
		properties: [
			{
				displayName: 'Client',
				name: 'client',
				type: 'string',
				default: 'research',
				required: true,
				description:
					'Registered client name in the gateway registry (unknown names fail with a 422 listing the valid names)',
			},
			{
				displayName: 'Purpose',
				name: 'purpose',
				type: 'string',
				default: 'n8n-chat',
				description: 'The call_id purpose segment (naming discipline of the evidence chain)',
			},
			{
				displayName: 'Options',
				name: 'options',
				type: 'collection',
				placeholder: 'Add option',
				default: {},
				options: [
					{
						displayName: 'Run ID',
						name: 'runId',
						type: 'string',
						default: '',
						description:
							'Route the calls into an active user run; empty routes to the ambient run',
					},
					{
						displayName: 'Task ID',
						name: 'taskId',
						type: 'string',
						default: '',
						description:
							'Attribute calls to an existing task hot record (unknown ids fail with 404)',
					},
					{
						displayName: 'Timeout (Seconds)',
						name: 'timeout',
						type: 'number',
						default: 300,
						description: 'Per-request transport timeout',
					},
				],
			},
		],
	};

	async supplyData(this: ISupplyDataFunctions, itemIndex: number): Promise<SupplyData> {
		const credentials = await this.getCredentials('ordigovernanceApi');
		const client = this.getNodeParameter('client', itemIndex) as string;
		const purpose = this.getNodeParameter('purpose', itemIndex) as string;
		const options = this.getNodeParameter('options', itemIndex, {}) as {
			runId?: string;
			taskId?: string;
			timeout?: number;
		};

		const model = new TrackedChatModel({
			baseUrl: String(credentials.baseUrl ?? ''),
			token: String(credentials.token ?? ''),
			client,
			purpose,
			runId: options.runId?.trim() || undefined,
			taskId: options.taskId?.trim() || undefined,
			timeout: options.timeout,
		});
		return { response: model };
	}
}