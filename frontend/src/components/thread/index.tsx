import { FormEvent, useRef, useEffect } from "react";
import { v4 as uuidv4 } from "uuid";
import { Checkpoint, Message } from "@langchain/langgraph-sdk";
import { parseAsBoolean, useQueryState } from "nuqs";
import { Send, LoaderCircle } from "lucide-react";
import { useStreamContext } from "@/providers/Stream";
import {
  DO_NOT_RENDER_ID_PREFIX,
  ensureToolCallsHaveResponses,
} from "@/lib/ensure-tool-responses";
import { AssistantMessage } from "./messages/ai";
import { HumanMessage } from "./messages/human";

export function Thread({
  input,
  setInput,
  request,
  onSubmitted,
}: {
  input: string;
  setInput: (value: string) => void;
  request?: import("@/providers/Stream").StateType["request"];
  onSubmitted?: () => void;
}) {
  const stream = useStreamContext();
  const [hideToolCalls, setHideToolCalls] = useQueryState(
    "hideToolCalls",
    parseAsBoolean.withDefault(true),
  );
  const bottom = useRef<HTMLDivElement>(null);
  const submitting = useRef(false);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [stream.messages.length]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() || stream.isLoading || submitting.current) return;
    submitting.current = true;
    const message: Message = {
      id: uuidv4(),
      type: "human",
      content: input.trim(),
    };
    const toolMessages = ensureToolCallsHaveResponses(stream.messages);
    setInput("");
    try {
      await stream.submit(
        { messages: [...toolMessages, message], request },
        {
          streamMode: ["values"],
          streamSubgraphs: true,
          streamResumable: true,
          optimisticValues: (prev) => ({
            ...prev,
            messages: [...(prev.messages ?? []), ...toolMessages, message],
          }),
        },
      );
      onSubmitted?.();
    } catch {
      setInput(typeof message.content === "string" ? message.content : "");
    } finally {
      submitting.current = false;
    }
  };
  const regenerate = (checkpoint: Checkpoint | null | undefined) => {
    if (!stream.isLoading)
      stream.submit(undefined, {
        checkpoint,
        streamMode: ["values"],
        streamSubgraphs: true,
        streamResumable: true,
      });
  };
  return (
    <section
      className="wb-chat"
      aria-label="科研对话"
    >
      <header>
        <strong>科研对话</strong>
        <span className="wb-muted">Orchestrator</span>
      </header>
      <div
        className="wb-messages"
        aria-live="polite"
      >
        {!stream.messages.length && <p className="wb-empty">尚无会话消息</p>}
        {stream.messages
          .filter((m) => !m.id?.startsWith(DO_NOT_RENDER_ID_PREFIX))
          .map((message, index) =>
            message.type === "human" ? (
              <HumanMessage
                key={message.id ?? index}
                message={message}
                isLoading={stream.isLoading}
              />
            ) : (
              <AssistantMessage
                key={message.id ?? index}
                message={message}
                isLoading={stream.isLoading}
                handleRegenerate={regenerate}
              />
            ),
          )}
        {!stream.messages.some((m) => m.type === "ai" || m.type === "tool") &&
          !!stream.interrupt && (
            <AssistantMessage
              message={undefined}
              isLoading={stream.isLoading}
              handleRegenerate={regenerate}
            />
          )}
        {stream.isLoading && (
          <p
            role="status"
            className="wb-muted"
          >
            <LoaderCircle
              size={14}
              className="animate-spin"
            />{" "}
            分析进行中
          </p>
        )}
        {!!stream.error && (
          <p
            role="alert"
            className="wb-warning"
          >
            连接或运行异常：
            {String(
              stream.error instanceof Error
                ? stream.error.message
                : stream.error,
            )}
            。请在运行页检查状态，避免重复提交。
          </p>
        )}
        <div ref={bottom} />
      </div>
      <form
        onSubmit={submit}
        className="wb-composer"
      >
        <textarea
          aria-label="研究问题"
          placeholder="提出科研问题…"
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <div className="wb-toolbar">
          <label>
            <input
              type="checkbox"
              checked={!hideToolCalls}
              onChange={(e) => setHideToolCalls(!e.target.checked)}
            />{" "}
            工具记录
          </label>
          {stream.isLoading ? (
            <button
              type="button"
              className="wb-icon"
              title="运行进行中，取消操作尚未接入"
              aria-label="运行进行中"
              disabled
            >
              <LoaderCircle
                size={17}
                className="animate-spin"
              />
            </button>
          ) : (
            <button
              className="wb-primary wb-icon"
              type="submit"
              disabled={!input.trim()}
              title="提交问题"
              aria-label="提交问题"
            >
              <Send size={17} />
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
