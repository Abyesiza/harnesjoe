import { API_URL } from "@/lib/server-env";

const LOCAL = new Set(["localhost", "127.0.0.1"]);

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const host = (request.headers.get("host") ?? "").replace(/:\d+$/, "");
  if (!LOCAL.has(host)) return Response.json({ detail: "Forbidden host." }, { status: 403 });
  const page = await (await fetch(`${API_URL}/`, { cache: "no-store" })).text();
  const token = page.match(/<meta name="fam-token" content="([^"]+)"/)?.[1];
  if (!token) return Response.json({ detail: "Backend gave no token." }, { status: 502 });
  return Response.json({ token }, { headers: { "Cache-Control": "no-store" } });
}
