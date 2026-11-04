// Where the manual lives.
//
// It used to be a page of this app (`#/docs`). It is now a page of the public
// site, in five languages and with no session, because a manual behind a login
// is a manual nobody reads before asking for access. The text is still generated
// from `backend/src/gateway/docs/` — the same files the console MCP searches —
// so this is a change of address, not of content.
//
// One constant, because both the landing and the `/docs` redirect point at it and
// two copies of a URL is how they end up pointing at two sites.
export const DOCS_SITE = "https://omg.giomartins.dev/docs/";
