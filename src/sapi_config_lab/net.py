"""Outbound HTTP for every stage. Redirects are never followed: a redirected
request could reach a host the caller never named."""

from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


urlopen = build_opener(NoRedirect).open
