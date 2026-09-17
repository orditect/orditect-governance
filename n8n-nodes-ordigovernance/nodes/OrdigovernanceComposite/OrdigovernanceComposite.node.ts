import type {
	IDataObject,
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
} from 'n8n-workflow';
import { NodeOperationError } from 'n8n-workflow';
import { gatewayRequest, pollUntil, type GatewayCredentials } from '../shared/gatewayHttp';
import { asTrimmedString, parseJsonObject } from '../shared/nodeParams';

const CREDENTIAL_TYPE = 'ordigovernanceApi';

// Gateway composites contract (D10, frozen):
//   POST /runs/{id}/composites {name, params}        -> {composite_id, ...}
//   GET  /runs/{id}/composites/{cid}                 -> {status, children?, outcome?}
// Children attach to the run root; evidence closes through the child
// tasks. A composite failure lands on the composite's own status,
// never on the run.
const pathComposites = (runId: string): string =>
	`/runs/${encodeURIComponent(runId)}/composites`;
const pathComposite = (runId: string, compositeId: string): string =>
	`/runs/${encodeURIComponent(runId)}/composites/${encodeURIComponent(compositeId)}`;

const TERMINAL_COMPOSITE_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);

function extractCompositeId(response: Record<string, unknown>): string {
	const cid = response.composite_id ?? response.id;
	if (typeof cid !== 'string' || !cid) {
		throw new Error('gateway response did not contain a composite_id');
	}
	return cid;
}

export class OrdigovernanceComposite implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Ordigovernance Composite',
		name: 'ordigovernanceComposite',
		icon: 'fa:layer-group',
		group: ['transform'],
		version: 1,
		subtitle: '={{ $parameter["compositeName"] }}',
		description:
			'Start a drive-level composite (e.g. a quality gate) on the Ordigovernance gateway and wait for its outcome',
		defaults: { name: 'Ordigovernance Composite' },
		inputs: ['main'],
		outputs: ['main'],
		credentials: [{ name: CREDENTIAL_TYPE, required: true }],
		properties: [
			{
				displayName: 'Run ID',
				name: 'runId',
				type: 'string',
				default: '',
				required: true,
				description: 'Identifier of the run the composite drives children into',
			},
			{
				displayName: 'Composite Name',
				name: 'compositeName',
				type: 'string',
				default: '',
				required: true,
				description:
					'Registered composite name (see GET /runs/{id}/vocabulary; unknown names fail with a 422 listing the valid names)',
			},
			{
				displayName: 'Params',
				name: 'compositeParams',
				type: 'json',
				default: '{}',
				description:
					'JSON params for the composite driver (e.g. producer_id / judge_id / threshold for quality_gate_pair)',
			},
			{
				displayName: 'Wait for Completion',
				name: 'waitForCompletion',
				type: 'boolean',
				default: true,
				description: 'Whether to poll the gateway until the composite reaches a terminal state',
			},
			{
				displayName: 'Poll Interval (Ms)',
				name: 'pollIntervalMs',
				type: 'number',
				default: 3000,
				displayOptions: { show: { waitForCompletion: [true] } },
				description: 'Delay between composite status polls',
			},
			{
				displayName: 'Poll Timeout (Ms)',
				name: 'pollTimeoutMs',
				type: 'number',
				default: 900000,
				displayOptions: { show: { waitForCompletion: [true] } },
				description: 'Maximum time to wait for a terminal composite state before failing',
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
				const runId = asTrimmedString(this.getNodeParameter('runId', itemIndex));
				const compositeName = asTrimmedString(this.getNodeParameter('compositeName', itemIndex));
				const compositeParams = parseJsonObject(
					this.getNodeParameter('compositeParams', itemIndex),
					'compositeParams',
				);
				const waitForCompletion = this.getNodeParameter(
					'waitForCompletion',
					itemIndex,
				) as boolean;

				const started = await gatewayRequest(credentials, {
					method: 'POST',
					path: pathComposites(runId),
					body: { name: compositeName, params: compositeParams },
				});
				const compositeId = extractCompositeId(started);

				let output: Record<string, unknown> = started;
				if (waitForCompletion) {
					const pollIntervalMs = this.getNodeParameter('pollIntervalMs', itemIndex) as number;
					const pollTimeoutMs = this.getNodeParameter('pollTimeoutMs', itemIndex) as number;
					const terminal = await pollUntil(
						() =>
							gatewayRequest(credentials, {
								method: 'GET',
								path: pathComposite(runId, compositeId),
							}),
						(composite) => TERMINAL_COMPOSITE_STATUSES.has(String(composite.status)),
						{ intervalMs: pollIntervalMs, timeoutMs: pollTimeoutMs },
						`composite ${compositeId}`,
					);
					output = { ...terminal, composite_id: compositeId };
					// A terminal state is not a success: surface non-succeeded
					// outcomes as node errors so the canvas stops lying.
					if (String(terminal.status) !== 'succeeded') {
						throw new Error(
							`composite ${compositeId} reached terminal status "${String(terminal.status)}"`,
						);
					}
				}

				returnData.push({ json: output as IDataObject, pairedItem: { item: itemIndex } });
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