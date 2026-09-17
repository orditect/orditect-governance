import type {
	IDataObject,
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
} from 'n8n-workflow';
import { NodeOperationError } from 'n8n-workflow';
import { gatewayRequest, type GatewayCredentials } from '../shared/gatewayHttp';
import { parseJsonObject } from '../shared/nodeParams';

const CREDENTIAL_TYPE = 'ordigovernanceApi';
const PATH_RUNS = '/runs';
const pathRunFinish = (runId: string): string => `/runs/${encodeURIComponent(runId)}/finish`;

export class OrdigovernanceRun implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Ordigovernance Run',
		name: 'ordigovernanceRun',
		icon: 'fa:play',
		group: ['transform'],
		version: 1,
		subtitle: '={{ $parameter["operation"] }}',
		description: 'Start or finish a governed run on the Ordigovernance gateway',
		defaults: { name: 'Ordigovernance Run' },
		inputs: ['main'],
		outputs: ['main'],
		credentials: [{ name: CREDENTIAL_TYPE, required: true }],
		properties: [
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				options: [
					{ name: 'Start', value: 'start', action: 'Start a governed run' },
					{ name: 'Finish', value: 'finish', action: 'Finish a governed run' },
				],
				default: 'start',
			},
			{
				displayName: 'Client',
				name: 'client',
				type: 'string',
				default: '',
				required: true,
				displayOptions: { show: { operation: ['start'] } },
				description: 'Client identifier recorded on the run',
			},
			{
				displayName: 'Purpose',
				name: 'purpose',
				type: 'string',
				default: '',
				required: true,
				displayOptions: { show: { operation: ['start'] } },
				description: 'Purpose label recorded on the run',
			},
			{
				displayName: 'Metadata',
				name: 'metadata',
				type: 'json',
				default: '{}',
				displayOptions: { show: { operation: ['start'] } },
				description: 'Optional JSON metadata attached to the run',
			},
			{
				displayName: 'Finish Existing on Conflict',
				name: 'finishExistingOnConflict',
				type: 'boolean',
				default: false,
				displayOptions: { show: { operation: ['start'] } },
				description:
					'Whether to cancel a stale in-progress run and retry when the gateway rejects start with 409. Use for interactive development; keep off in production so conflicts surface loudly.',
			},

			{
				displayName: 'Run ID',
				name: 'runId',
				type: 'string',
				default: '',
				required: true,
				displayOptions: { show: { operation: ['finish'] } },
				description: 'Identifier of the run to finish',
			},
			{
				displayName: 'Final Status',
				name: 'finalStatus',
				type: 'options',
				options: [
					{ name: 'Succeeded', value: 'succeeded' },
					{ name: 'Failed', value: 'failed' },
					{ name: 'Cancelled', value: 'cancelled' },
				],
				default: 'succeeded',
				displayOptions: { show: { operation: ['finish'] } },
				description: 'Terminal status reported for the run',
			},
			{
				displayName: 'Summary',
				name: 'summary',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['finish'] } },
				description: 'Optional human-readable summary of the run outcome',
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const credentials = (await this.getCredentials(
			CREDENTIAL_TYPE,
		)) as unknown as GatewayCredentials;
		const returnData: INodeExecutionData[] = [];

		for (let itemIndex = 0; itemIndex < items.length; itemIndex++) {
			try {
				const operation = this.getNodeParameter('operation', itemIndex) as string;
				let response: Record<string, unknown>;

				if (operation === 'start') {
					const client = this.getNodeParameter('client', itemIndex) as string;
					const purpose = this.getNodeParameter('purpose', itemIndex) as string;
					const metadata = parseJsonObject(
						this.getNodeParameter('metadata', itemIndex),
						'metadata',
					);
					const finishExisting = this.getNodeParameter(
						'finishExistingOnConflict',
						itemIndex,
						false,
					) as boolean;

					try {
						response = await gatewayRequest(credentials, {
							method: 'POST',
							path: PATH_RUNS,
							body: { client, purpose, metadata },
						});
					} catch (error) {
						// Single-active-run gateway: on 409, optionally cancel the
						// stale run parsed from the error detail and retry once.
						const message = error instanceof Error ? error.message : String(error);
						const conflictRunId = message.match(/\[409\].*'([^']+)'/)?.[1];
						if (!finishExisting || !conflictRunId) throw error;

						await gatewayRequest(credentials, {
							method: 'POST',
							path: pathRunFinish(conflictRunId),
							body: { status: 'cancelled', summary: 'auto-cancelled by n8n retry' },
						});
						response = await gatewayRequest(credentials, {
							method: 'POST',
							path: PATH_RUNS,
							body: { client, purpose, metadata },
						});
					}
				} else {
					const runId = (this.getNodeParameter('runId', itemIndex) as string).trim();
					const finalStatus = this.getNodeParameter('finalStatus', itemIndex) as string;
					const summary = this.getNodeParameter('summary', itemIndex) as string;
					const body: Record<string, unknown> = { status: finalStatus };
					if (summary) body.summary = summary;
					response = await gatewayRequest(credentials, {
						method: 'POST',
						path: pathRunFinish(runId),
						body,
					});
				}

				returnData.push({ json: response as IDataObject, pairedItem: { item: itemIndex } });
			} catch (error) {
				if (this.continueOnFail()) {
					returnData.push({
						json: { error: error instanceof Error ? error.message : String(error) },
						pairedItem: { item: itemIndex },
					});
					continue;
				}
				throw new NodeOperationError(
					this.getNode(),
					error instanceof Error ? error.message : String(error),
					{ itemIndex },
				);
			}
		}

		return [returnData];
	}
}