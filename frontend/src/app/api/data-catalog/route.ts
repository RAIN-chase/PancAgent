export const runtime = "nodejs";

export async function GET(request: Request) {
  const base = process.env.PANCAGENT_BACKEND_URL;
  if (!base)
    return Response.json({ configured: false, collections: [], datasets: [] });
  const url = new URL("data-catalog", base.endsWith("/") ? base : `${base}/`);
  url.searchParams.set(
    "project_id",
    new URL(request.url).searchParams.get("project_id") ?? "default",
  );
  try {
    const response = await fetch(url, {
      headers: { Authorization: request.headers.get("authorization") ?? "" },
      cache: "no-store",
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok)
      return Response.json(
        { detail: "数据目录不可用或无访问权限" },
        { status: response.status },
      );
    return Response.json(await response.json(), {
      headers: { "Cache-Control": "private, no-store" },
    });
  } catch {
    return Response.json({ detail: "无法连接数据目录服务" }, { status: 503 });
  }
}
