# import asyncio
# from clio_web_search.config import Settings
# from clio_web_search.doi import resolve_doi

# async def main():
#     settings = Settings()  # your .env se settings uthayega
#     result = await resolve_doi("10.1038/s41586-021-03819-2", settings)
#     print("sources_queried:", result["sources_queried"])
#     print("title:", result["metadata"].get("title"))
#     print("candidates:")
#     for c in result["candidates"]:
#         print(" -", c["source"], c["url"])

# asyncio.run(main())