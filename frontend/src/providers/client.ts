import { Client } from "@langchain/langgraph-sdk";

export function authHeaders(
  apiKey?: string | null,
  authScheme?: string,
): Record<string, string> {
  if (authScheme === "bearer") {
    return apiKey ? { Authorization: `Bearer ${apiKey}` } : {};
  }
  return {
    ...(apiKey && { "X-Api-Key": apiKey }),
    ...(authScheme && { "X-Auth-Scheme": authScheme }),
  };
}

export function createClient(
  apiUrl: string,
  apiKey: string | undefined,
  authScheme: string | undefined,
) {
  return new Client({
    apiKey: authScheme === "bearer" ? undefined : apiKey,
    apiUrl,
    defaultHeaders: authHeaders(apiKey, authScheme),
  });
}
