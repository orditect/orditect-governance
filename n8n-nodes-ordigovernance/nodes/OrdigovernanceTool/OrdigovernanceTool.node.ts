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
const PATH_TOOL_CALL = '/governed/tool-call';

export class OrdigovernanceTool implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Ordigovernance Tool',
		name: 'ordigovernanceTool',
		icon: 'fa:wrench',
		group: ['transform'],
		version: 1,
		subtitle: '={{ $parameter["toolName"] }}',
		description: 'Invoke a tool through the Ordigovernance gateway governance path',
		defaults: { name: 'Ordigovernance Tool' },
		inputs: ['main'],
		outputs: ['main'],
		credentials: [{ name: CREDENTIAL_TYPE, required: true }],
		properties: [
			{
				displayName: 'Client',
				name: 'client',
				type: 'string',
				default: '',
				required: true,
				description: 'Client identifier recorded on the tool call',
			},
			{
				displayName: 'Purpose',
				name: 'purpose',
				type: 'string',
				default: '',
				required: true,
				description: 'Purpose label recorded on the tool call',
			},
			{
				displayName: 'Tool Name',
				name: 'toolName',
				type: 'string',
				default: '',
				required: true,
				description: 'Name of the registered tool to invoke',
			},
			{
				displayName: 'Inputs',
				name: 'toolInputs',
				type: 'json',
				default: '{}',
				description:
					'JSON object of keyword arguments expanded into the tool handler',
			},
			{
				displayName: 'Run ID',
				name: 'runId',
				type: 'string',
				default: '',
				description: 'Optional run identifier to link this tool call to',
			},
			{
				displayName: 'Task ID',
				name: 'taskId',
				type: 'string',
				default: '',
				description: 'Optional task identifier to link this tool call to',
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
				const client = this.getNodeParameter('client', itemIndex) as string;
				const purpose = this.getNodeParameter('purpose', itemIndex) as string;
				const toolName = this.getNodeParameter('toolName', itemIndex) as string;
				const toolInputs = parseJsonObject(
					this.getNodeParameter('toolInputs', itemIndex),
					'toolInputs',
				);
				const runId = (this.getNodeParameter('runId', itemIndex) as string).trim();
				const taskId = (this.getNodeParameter('taskId', itemIndex) as string).trim();

				// Gateway contract: tool arguments travel under "inputs" and are
				// expanded into the handler as keyword arguments.
				const body: Record<string, unknown> = {
					client,
					purpose,
					tool: toolName,
					inputs: toolInputs,
				};
				if (runId) body.run_id = runId;
				if (taskId) body.task_id = taskId;

				const response = await gatewayRequest(credentials, {
					method: 'POST',
					path: PATH_TOOL_CALL,
					body,
				});
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