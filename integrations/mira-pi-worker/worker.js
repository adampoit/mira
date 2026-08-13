#!/usr/bin/env node

import process from "node:process";
import { Type } from "typebox";
import {
  createAgentSession,
  DefaultResourceLoader,
  defineTool,
  ModelRuntime,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";

const pending = new Map();
let nextRequestId = 1;
let configured = false;
let resolveConfig;
let rejectConfig;
const configPromise = new Promise((resolve, reject) => {
  resolveConfig = resolve;
  rejectConfig = reject;
});
const send = (message) => process.stdout.write(`${JSON.stringify(message)}\n`);
const REPOSITORY_METHODS = new Set(["read", "grep", "find", "ls"]);
const MAX_SUBMISSION_NUDGES = 2;
const MAX_TRACE_VALUE_CHARS = 20000;

function traceValue(value) {
  if (value === undefined) return null;
  if (typeof value === "string")
    return value.length > MAX_TRACE_VALUE_CHARS
      ? `${value.slice(0, MAX_TRACE_VALUE_CHARS)}… [truncated]`
      : value;
  try {
    const encoded = JSON.stringify(value);
    if (typeof encoded !== "string") return String(value);
    return encoded.length > MAX_TRACE_VALUE_CHARS
      ? `${encoded.slice(0, MAX_TRACE_VALUE_CHARS)}… [truncated]`
      : value;
  } catch {
    return String(value);
  }
}

function validateConfig(message) {
  if (message.type !== "start") throw new Error("Expected start message");
  if (typeof message.model !== "string" || !message.model)
    throw new Error("Mira must provide a Pi model");
  if (typeof message.thinking_level !== "string")
    throw new Error("Mira must provide a Pi thinking level");
  if (!Array.isArray(message.tools))
    throw new Error("Mira must provide Pi tools");
  if (typeof message.result_tool !== "string" || !message.result_tool)
    throw new Error("Mira must identify the result submission tool");

  const toolNames = message.tools.map((spec) => spec?.function?.name);
  if (toolNames.filter((name) => name === message.result_tool).length !== 1)
    throw new Error("The result submission tool must appear exactly once");
  const unsupported = toolNames.filter(
    (name) => name !== message.result_tool && !REPOSITORY_METHODS.has(name),
  );
  if (unsupported.length)
    throw new Error(`Pi cannot execute host tool: ${unsupported.join(", ")}`);
  return message;
}

function handleInput(line) {
  if (!line) return;
  try {
    const message = JSON.parse(line.endsWith("\r") ? line.slice(0, -1) : line);
    if (!configured) {
      const config = validateConfig(message);
      configured = true;
      resolveConfig(config);
      return;
    }
    if (message.type !== "tool_response") return;
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    if (message.error) request.reject(new Error(message.error));
    else request.resolve(message.result);
  } catch (error) {
    if (!configured) {
      configured = true;
      rejectConfig(error);
    }
  }
}

let inputBuffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  inputBuffer += chunk;
  let newlineIndex;
  while ((newlineIndex = inputBuffer.indexOf("\n")) !== -1) {
    const line = inputBuffer.slice(0, newlineIndex);
    inputBuffer = inputBuffer.slice(newlineIndex + 1);
    handleInput(line);
  }
});
process.stdin.on("end", () => {
  if (inputBuffer) handleInput(inputBuffer);
});

function rpc(method, params) {
  const id = nextRequestId++;
  send({ type: "tool_request", id, method, params });
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

function systemInstructions(messages) {
  return messages
    .filter((message) => message.role === "system")
    .map((message) =>
      typeof message.content === "string"
        ? message.content
        : JSON.stringify(message.content),
    )
    .join("\n\n");
}

function promptFromMessages(messages) {
  return messages
    .filter((message) => message.role !== "system")
    .map((message) => {
      const role = message.role || "user";
      const content =
        typeof message.content === "string"
          ? message.content
          : JSON.stringify(message.content);
      return `<${role}>\n${content}\n</${role}>`;
    })
    .join("\n\n");
}

async function main() {
  const config = await configPromise;
  let submittedResult;
  const tools = config.tools.map((spec) => {
    const fn = spec.function || {};
    return defineTool({
      name: fn.name,
      label: fn.name,
      description: fn.description || fn.name,
      parameters: Type.Unsafe(
        fn.parameters || { type: "object", properties: {} },
      ),
      execute: async (_toolCallId, params) => {
        if (REPOSITORY_METHODS.has(fn.name)) {
          return {
            content: [
              { type: "text", text: String(await rpc(fn.name, params)) },
            ],
            details: {},
          };
        }
        if (fn.name !== config.result_tool)
          throw new Error(`Pi cannot execute host tool: ${fn.name}`);
        submittedResult = JSON.stringify(params);
        return {
          content: [{ type: "text", text: "Result accepted." }],
          details: {},
          terminate: true,
        };
      },
    });
  });

  const modelRuntime = await ModelRuntime.create({
    authPath: `${config.agent_dir}/auth.json`,
    modelsPath: null,
    refreshOnCreate: false,
  });
  if (process.env.OPENCODE_API_KEY)
    await modelRuntime.setRuntimeApiKey(
      "opencode-go",
      process.env.OPENCODE_API_KEY,
    );
  const modelId = config.model.split("/").at(-1);
  const model = modelRuntime.getModel("opencode-go", modelId);
  if (!model) throw new Error(`Pi model not found: opencode-go/${modelId}`);

  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: true },
    retry: { enabled: true, maxRetries: 2 },
  });
  const miraInstructions = systemInstructions(config.messages || []);
  const resourceLoader = new DefaultResourceLoader({
    cwd: config.cwd,
    agentDir: config.agent_dir,
    settingsManager,
    appendSystemPromptOverride: (base) => [
      ...base,
      ...(miraInstructions
        ? [`## Mira pipeline instructions\n\n${miraInstructions}`]
        : []),
    ],
  });
  await resourceLoader.reload();

  const { session } = await createAgentSession({
    cwd: config.cwd,
    agentDir: config.agent_dir,
    model,
    thinkingLevel: config.thinking_level,
    tools: tools.map((tool) => tool.name),
    customTools: tools,
    noTools: "builtin",
    modelRuntime,
    resourceLoader,
    sessionManager: SessionManager.create(config.cwd, config.session_dir),
    settingsManager,
  });

  let textBuffer = "";
  let thinkingBuffer = "";
  let lastFlush = Date.now();
  const flush = (force = false) => {
    const buffered = textBuffer.length + thinkingBuffer.length;
    const hasBoundary =
      textBuffer.includes("\n") || thinkingBuffer.includes("\n");
    if (
      !force &&
      buffered < 600 &&
      !hasBoundary &&
      Date.now() - lastFlush < 2000
    )
      return;
    lastFlush = Date.now();
    if (thinkingBuffer) {
      send({ type: "thinking_delta", delta: thinkingBuffer });
      thinkingBuffer = "";
    }
    if (textBuffer) {
      send({ type: "text_delta", delta: textBuffer });
      textBuffer = "";
    }
  };
  const timer = setInterval(flush, 300);
  session.subscribe((event) => {
    if (event.type === "message_update") {
      const assistantEvent = event.assistantMessageEvent;
      if (assistantEvent.type === "thinking_delta")
        thinkingBuffer += assistantEvent.delta;
      if (assistantEvent.type === "text_delta")
        textBuffer += assistantEvent.delta;
      if (
        assistantEvent.type === "thinking_start" ||
        assistantEvent.type === "thinking_end"
      ) {
        flush(true);
        send({
          type: "stream_boundary",
          channel: "thinking",
          boundary: assistantEvent.type === "thinking_start" ? "start" : "end",
        });
      }
      if (
        assistantEvent.type === "text_start" ||
        assistantEvent.type === "text_end"
      ) {
        flush(true);
        send({
          type: "stream_boundary",
          channel: "text",
          boundary: assistantEvent.type === "text_start" ? "start" : "end",
        });
      }
    } else if (event.type === "tool_execution_start") {
      flush(true);
      send({
        type: "tool_start",
        id: event.toolCallId,
        tool: event.toolName,
        args: traceValue(event.args),
      });
    } else if (event.type === "tool_execution_end") {
      flush(true);
      send({
        type: "tool_end",
        id: event.toolCallId,
        tool: event.toolName,
        result: traceValue(event.result),
        is_error: event.isError,
      });
    }
  });

  try {
    await session.prompt(promptFromMessages(config.messages || []), {
      expandPromptTemplates: false,
    });
    for (
      let attempt = 0;
      submittedResult === undefined && attempt < MAX_SUBMISSION_NUDGES;
      attempt += 1
    ) {
      await session.prompt(
        `Finish the requested work by calling ${config.result_tool}.`,
        { expandPromptTemplates: false },
      );
    }
    if (submittedResult === undefined)
      throw new Error(
        `Pi pass finished without an accepted ${config.result_tool} call`,
      );
    flush(true);
    const sessionTokens = session.getSessionStats().tokens;
    send({
      type: "done",
      result: submittedResult,
      usage: {
        input: sessionTokens.input,
        output: sessionTokens.output,
        cacheRead: sessionTokens.cacheRead,
        cacheWrite: sessionTokens.cacheWrite,
        total: sessionTokens.total,
      },
    });
  } finally {
    clearInterval(timer);
    flush(true);
    session.dispose();
    process.stdin.pause();
  }
}

main().catch((error) => {
  send({
    type: "error",
    error: error instanceof Error ? error.message : String(error),
  });
  process.stdin.pause();
  process.exitCode = 1;
});
