---
name: mcp-builder
description: Build a Model Context Protocol server to wire an external API or service into LLM tools.
when_to_trigger:
  - mcp server
  - integrate external api
  - model context protocol
  - build mcp
kind: magic_skill
attribution: https://github.com/modelcontextprotocol/servers
---

# MCP Builder

## When to use

Use when you need an LLM tool (Claude, Cursor, Zed) to interact with an external service that does not already have an MCP server. Building an MCP server once makes that service first-class across every MCP-compatible client. Skip this when an official MCP server for the service already exists — use theirs.

## Process

1. **Pick the transport and SDK.** Stdio for local servers (simplest), HTTP+SSE for hosted. Python SDK (`mcp`), TypeScript SDK (`@modelcontextprotocol/sdk`), or Go SDK. For agent-driven projects, TypeScript + stdio is the fastest path.
2. **Define tools, resources, and prompts separately.**
   - **Tools** are functions the LLM can call (`create_issue`, `list_tasks`). Typed inputs, typed outputs.
   - **Resources** are readable data the LLM can request (documents, records). URI-addressable.
   - **Prompts** are parameterized system messages the LLM can reuse.
   Most simple servers expose tools only; add resources/prompts when they pay for themselves.
3. **Type everything with Zod (TS) or Pydantic (Py).** The SDK uses your schema for both validation and for telling the client what the tool accepts.
4. **Keep each tool single-purpose.** `create_task` takes title + project; do not combine with `list_tasks`. The LLM picks tools by description; fewer-responsibility tools get picked correctly more often.
5. **Write good descriptions.** The `description` field on each tool is how the LLM decides when to invoke it. "Creates a new task in the specified project" beats "Create task." The description is the API doc for the LLM.
6. **Handle errors as tool responses, not exceptions.** MCP expects the tool to return `{ isError: true, content: [{ type: 'text', text: 'message' }] }` — the LLM sees the error text and can recover. Uncaught exceptions crash the transport.
7. **Test with `@modelcontextprotocol/inspector`.** `npx @modelcontextprotocol/inspector node server.js` gives you an interactive UI to call tools and see responses. Faster than Claude-as-the-test-harness.
8. **Publish via `mcpServers` config.** Document how to add the server in `claude_desktop_config.json` / Cursor / Zed. Include `command`, `args`, `env` requirements.

## Example invocation

```ts
// server.ts — a minimal MCP server wrapping a fictional tasks API
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { z } from 'zod';

const server = new Server({ name: 'polly-mcp', version: '1.0.0' }, { capabilities: { tools: {} } });

const CreateTaskInput = z.object({
  title: z.string().min(1).max(200),
  project_id: z.string().uuid(),
});

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'create_task',
      description: 'Create a new task in a specific project. Returns the created task with its id.',
      inputSchema: {
        type: 'object',
        properties: {
          title: { type: 'string', description: 'Title of the task (<= 200 chars)' },
          project_id: { type: 'string', description: 'UUID of the project' },
        },
        required: ['title', 'project_id'],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  if (request.params.name !== 'create_task') {
    return { isError: true, content: [{ type: 'text', text: `unknown tool: ${request.params.name}` }] };
  }
  const parsed = CreateTaskInput.safeParse(request.params.arguments);
  if (!parsed.success) {
    return { isError: true, content: [{ type: 'text', text: `invalid input: ${parsed.error.message}` }] };
  }
  try {
    const res = await fetch(`${process.env.POLLY_API}/v1/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${process.env.POLLY_TOKEN}` },
      body: JSON.stringify(parsed.data),
    });
    if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
    const task = await res.json();
    return { content: [{ type: 'text', text: JSON.stringify(task) }] };
  } catch (e: any) {
    return { isError: true, content: [{ type: 'text', text: e.message }] };
  }
});

await server.connect(new StdioServerTransport());
```

```json
// claude_desktop_config.json entry
{
  "mcpServers": {
    "polly": {
      "command": "node",
      "args": ["/path/to/server.js"],
      "env": { "POLLY_API": "https://api.polly.example.com", "POLLY_TOKEN": "..." }
    }
  }
}
```

## Outputs

- A running MCP server (stdio or HTTP).
- Typed tools with clear descriptions for LLM discovery.
- A README with install + config instructions.
- A test harness via `@modelcontextprotocol/inspector`.

## Common failure modes

- Uncaught exceptions crash the transport; return structured errors instead.
- Weak tool descriptions; LLM never picks the right tool.
- Bundling multiple operations per tool; LLM misuses them.
- No Inspector-based tests; bugs only discovered via the LLM harness.
