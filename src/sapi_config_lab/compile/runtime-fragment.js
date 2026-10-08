// One JSON envelope per logical execution. All branches return an envelope,
// including skipped branches. Transport is handled by n8n HTTP Request nodes.
function need(condition, message) { if (!condition) throw new Error(message); }
const clone = value => JSON.parse(JSON.stringify(value));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function mergeEnvelopes(items) {
  need(items.length > 0, 'Missing input envelope');
  const ctx = clone(items[0].json);
  for (const item of items.slice(1)) {
    const other = item.json;
    need(same(ctx.inputs, other.inputs), 'Join inputs disagree');
    if (ctx.refinement) {
      need(same(ctx.runtime, other.runtime) && same(ctx.refinement, other.refinement), 'Join attempt state disagrees');
      need(ctx.deadline_at_ms === other.deadline_at_ms, 'Join deadlines disagree');
    }
    for (const collection of ['steps', 'statuses', 'events']) {
      for (const [key, value] of Object.entries(other[collection])) {
        if (Object.hasOwn(ctx[collection], key)) need(same(ctx[collection][key], value), 'Conflicting branch output: ' + key);
        else ctx[collection][key] = clone(value);
      }
    }
  }
  return ctx;
}
function resolveRef(path, ctx, optional = false) {
  const parts = path.split('.');
  if (optional && parts[0] === 'steps' && ctx.statuses[parts[1]] === 'skipped') return null;
  let current = ctx;
  for (const part of parts) {
    need(current !== null && typeof current === 'object' && Object.hasOwn(current, part), 'Unavailable reference: ' + path);
    current = current[part];
  }
  return current;
}
function resolveValue(value, ctx) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    if (Object.hasOwn(value, 'ref')) return resolveRef(value.ref, ctx);
    if (Object.hasOwn(value, 'optional_ref')) return resolveRef(value.optional_ref, ctx, true);
    return Object.fromEntries(Object.entries(value).map(([key, v]) => [key, resolveValue(v, ctx)]));
  }
  if (Array.isArray(value)) return value.map(x => resolveValue(x, ctx));
  return value;
}
// Deliberately bounded JSON Schema subset. The compiler rejects unsupported
// schema keywords, so a constraint cannot silently disappear at execution.
function validateSchema(value, schema, path = 'output') {
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  const matches = type => {
    if (type === 'null') return value === null;
    if (type === 'object') return value !== null && typeof value === 'object' && !Array.isArray(value);
    if (type === 'array') return Array.isArray(value);
    if (type === 'integer') return Number.isSafeInteger(value);
    if (type === 'number') return typeof value === 'number' && Number.isFinite(value);
    return typeof value === type;
  };
  need(types.some(matches), 'Schema violation at ' + path + ': expected ' + types.join('|'));
  if (schema.enum) need(schema.enum.some(x => same(x, value)), 'Schema violation at ' + path + ': enum');
  if (typeof value === 'string' && schema.minLength !== undefined) need([...value].length >= schema.minLength, 'Schema violation at ' + path + ': minLength');
  if (typeof value === 'number' && schema.minimum !== undefined) need(value >= schema.minimum, 'Schema violation at ' + path + ': minimum');
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined) need(value.length >= schema.minItems, 'Schema violation at ' + path + ': minItems');
    if (schema.maxItems !== undefined) need(value.length <= schema.maxItems, 'Schema violation at ' + path + ': maxItems');
    value.forEach((item, i) => validateSchema(item, schema.items, path + '[' + i + ']'));
  } else if (value !== null && typeof value === 'object') {
    for (const key of schema.required || []) need(Object.hasOwn(value, key), 'Schema violation at ' + path + ': missing ' + key);
    for (const [key, child] of Object.entries(value)) {
      if (Object.hasOwn(schema.properties || {}, key)) validateSchema(child, schema.properties[key], path + '.' + key);
      else need(schema.additionalProperties !== false, 'Schema violation at ' + path + ': unexpected ' + key);
    }
  }
}
function shouldRun(step, ctx) {
  return !step.when || same(resolveRef(step.when.ref, ctx), step.when.eq);
}
function checkDeadline(ctx) {
  if (ctx.deadline_at_ms !== undefined) need(Date.now() < ctx.deadline_at_ms, 'Workflow deadline exceeded');
}
function recordStep(step, ctx, status, implementation, extra = {}) {
  need(!Object.hasOwn(ctx.statuses, step.id), 'Step executed more than once: ' + step.id);
  ctx.statuses[step.id] = status;
  ctx.events[step.id] = {step_id: step.id, operation: step.uses, status, actor: step.actor || null, implementation, ...extra};
  return ctx;
}
function attachOutput(step, ctx, output, binding) {
  need(output !== null && typeof output === 'object' && !Array.isArray(output), 'Operation must return an object');
  need(same(Object.keys(output).sort(), [...binding.outputs].sort()), 'Output fields differ from binding');
  if (binding.output_schema) validateSchema(output, binding.output_schema);
  ctx.steps[step.id] = output;
}
function applyStep(step, ctx, binding) {
  checkDeadline(ctx);
  let status = 'completed';
  if (!shouldRun(step, ctx)) status = 'skipped';
  if (status === 'completed') {
    const inputs = resolveValue(step.with, ctx);
    const output = operations[step.uses](inputs);
    // Keep operation-specific deterministic diagnostics, then enforce the same
    // catalog contract used before dispatching a live call.
    if (binding.input_schema) validateSchema(inputs, binding.input_schema, 'inputs');
    attachOutput(step, ctx, output, binding);
  }
  return recordStep(step, ctx, status, step.kind === 'LLM' ? 'stub' : 'script');
}
function prepareAgency(step, ctx, binding, invocationId) {
  checkDeadline(ctx);
  const should_run = shouldRun(step, ctx);
  if (!should_run) return {envelope: ctx, should_run, request: null};
  const inputs = resolveValue(step.with, ctx);
  validateSchema(inputs, binding.input_schema, 'inputs');
  return {envelope: ctx, should_run, request: {
    invocation_id: invocationId, operation: step.uses, actor: step.actor || null, inputs,
  }};
}
function restoreAgency(step, prepared, response, binding) {
  const ctx = clone(prepared.envelope);
  checkDeadline(ctx);
  if (!prepared.should_run) return recordStep(step, ctx, 'skipped', 'live');
  need(response && typeof response === 'object' && !Array.isArray(response), 'Invalid Agency response');
  need(Object.keys(response).every(k => ['invocation_id', 'status', 'output', 'usage'].includes(k)), 'Unexpected Agency response fields');
  need(response.invocation_id === prepared.request.invocation_id, 'Agency invocation ID mismatch');
  need(response.status === 'completed', 'Agency did not complete');
  attachOutput(step, ctx, response.output, binding);
  return recordStep(step, ctx, 'completed', 'live', {invocation_id: response.invocation_id});
}

// Generic candidate tool transport: preserve arbitrary response data as JSON text.
function prepareTool(step, ctx, binding, invocationId) {
  const prepared = prepareAgency(step, ctx, binding, invocationId);
  if (prepared.should_run) prepared.request = {operation: step.uses, arguments: prepared.request.inputs, operation_id: invocationId, max_attempts: binding.max_attempts || 1};
  return prepared;
}
function restoreTool(step, prepared, response, binding) {
  const ctx = clone(prepared.envelope);
  checkDeadline(ctx);
  if (!prepared.should_run) return recordStep(step, ctx, 'skipped', 'http');
  need(response && typeof response === 'object' && !Array.isArray(response) && typeof response.ok === 'boolean', 'Invalid tool response');
  need(same(Object.keys(response).sort(), response.ok ? ['ok','value'] : ['error','ok']), 'Unexpected tool response fields');
  if (!response.ok) {
    const error = response.error;
    need(error && typeof error === 'object' && !Array.isArray(error) && same(Object.keys(error).sort(), ['code','message','retryable']), 'Invalid tool error');
    need(typeof error.code === 'string' && typeof error.message === 'string' && typeof error.retryable === 'boolean', 'Invalid tool error fields');
  }
  attachOutput(step, ctx, {result_json: JSON.stringify(response)}, binding);
  return recordStep(step, ctx, 'completed', 'http');
}
