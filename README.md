rnhttp
======

Library for HTTP/1.1 over Reticulum. It provides both a server and client library as well as some example servers and a socks proxy client. You can run `python -m rnhttp.client` to make web requests against an arbirary server. The servers have a concept of the port they are hosting on as well. This will allow building applications over RNS that use the HTTP stack without having to build any sort of network proxying.
