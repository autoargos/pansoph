# pansoph-connector-weblink

The public-link fetcher (Tier 2, reach-anything). Ingests any directly-fetchable URL,
with no credentials. Config:

```
{ "urls": ["https://example.com/a", "https://example.com/b"], "public": true, "allow_groups": [] }
```

It fetches each URL, stores the response text, and applies the ACL from config
(public by default). It never bypasses authentication: it only fetches what is openly
reachable, and respects the server's response. HTML to clean-text extraction is a
follow-up; point it at text or markdown URLs for the cleanest result.
