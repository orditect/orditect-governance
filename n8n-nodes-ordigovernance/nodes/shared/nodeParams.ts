/**
 * Parameter parsing helpers shared across Ordigovernance nodes.
 */

/**
 * Accepts a value coming from an n8n "json" parameter (already-parsed object
 * or raw string) and normalizes it to a plain object. Throws a readable
 * error for anything else.
 */
export function parseJsonObject(value: unknown, parameterName: string): Record<string, unknown> {
	if (value === undefined || value === null || value === '') return {};
	if (typeof value === 'object' && !Array.isArray(value)) {
		return value as Record<string, unknown>;
	}
	if (typeof value === 'string') {
		try {
			const parsed: unknown = JSON.parse(value);
			if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
				return parsed as Record<string, unknown>;
			}
		} catch {
			// Fall through to the readable error below.
		}
	}
	throw new Error(`parameter "${parameterName}" must be a JSON object`);
}

/**
 * Normalizes a string node parameter by trimming surrounding whitespace.
 * n8n expression fields treat every character outside {{ }} as literal
 * text, so an indented expression silently leaks spaces into ids and
 * therefore into request paths.
 */
export function asTrimmedString(value: unknown): string {
	return typeof value === 'string' ? value.trim() : String(value ?? '');
}