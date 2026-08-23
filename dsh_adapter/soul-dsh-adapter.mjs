#!/usr/bin/env node
/**
 * dsh_adapter/soul-dsh-adapter.mjs
 * DSH-P0-1 — TypeScript soul-dsh-adapter（minimal，mock DSH execution）。
 *
 * 本檔是 Phase 0 的 minimal mock：證明 Python WorkKernel → BridgeMessage →
 * Adapter → HandoffResult → consume_handoff → WorkEvent 的完整 execution path。
 * **不實作真 DSH subagent / workflow / goal**（後續 phase 才做，且依 MA-4 §1.1
 * 是獨立 TypeScript DSH plugin package，不在 Soul OS repo 內）。
 *
 * 通訊協定（與 src/work_adapter/bridge.py 對齊，兩側 mirror BridgeMessage /
 * HandoffResult 這兩個 language-neutral JSON contract）：
 * - stdin  ：一行 BridgeMessage JSON（message_type=request）
 * - stdout ：一行 HandoffResult JSON
 * - 一 request 一 response，讀完 stdin（EOF）即結束。
 *
 * 無 durable write authority：
 * - 只 read/write stdin/stdout，**不 import fs、不碰任何 durable store**。
 * - durable write 一律回 Domain Core（WorkflowOrchestrator.consume_handoff）執行。
 *
 * mock execution 行為：
 * - 依 payload.capability 決定 result_type（含 "evidence" → evidence、
 *   含 "decision" → decision，其餘 → artifact）
 * - fake artifact/evidence ref = "mock:sha256:<sha256(request seed)>"：
 *   同 request → 同 ref，讓 Domain Core 的 idempotency dedup 在 bridge 全路徑命中
 *   （dedup 只吞 identical retry，不吞不同結果）。
 */
import { createHash } from "node:crypto";

function mockResultType(capability) {
  if (capability.includes("evidence")) return "evidence";
  if (capability.includes("decision")) return "decision";
  return "artifact";
}

function handle(input) {
  let message;
  try {
    message = JSON.parse(input);
  } catch (err) {
    process.stderr.write(
      `soul-dsh-adapter: malformed BridgeMessage JSON: ${err.message}\n`
    );
    process.exit(1);
  }

  const payload = message.payload || {};
  const workId = payload.work_id || "unknown";
  const role = payload.role || message.actor || "specialist";
  const capability = payload.capability || "artifact.create";
  const objective = payload.objective || "";

  // mock DSH execution：以 request 內容 hash 出 content-addressed fake ref
  const seed = JSON.stringify({ work_id: workId, role, capability, objective });
  const ref = "mock:sha256:" + createHash("sha256").update(seed).digest("hex");

  const resultType = mockResultType(capability);
  const handoff = {
    work_id: workId,
    role,
    result_type: resultType,
    artifact_refs: resultType === "artifact" ? [ref] : [],
    evidence_refs: resultType === "evidence" ? [ref] : [],
    decision:
      resultType === "decision"
        ? { mock: true, rationale: `mock decision for ${objective || workId}` }
        : {},
    status: "done",
    resume_hint: { execution: "mock", capability, artifact_ref: ref },
  };

  process.stdout.write(JSON.stringify(handoff) + "\n");
}

const chunks = [];
process.stdin.on("data", (chunk) => chunks.push(chunk));
process.stdin.on("end", () => {
  const input = Buffer.concat(chunks).toString("utf-8").trim();
  if (!input) {
    process.stderr.write("soul-dsh-adapter: empty stdin\n");
    process.exit(1);
  }
  handle(input);
});
process.stdin.on("error", (err) => {
  process.stderr.write(`soul-dsh-adapter: stdin error: ${err.message}\n`);
  process.exit(1);
});
