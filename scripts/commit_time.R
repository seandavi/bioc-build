#!/usr/bin/env Rscript
# Decode build-source's COMMITINFO (buildtools::commit_info_base64: url-safe
# base64 without padding, zlib stream, JSON with `time` in epoch seconds)
# and print the commit time as %Y-%m-%dT%H:%M:%SZ (UTC). Prints `null` if the
# argument is empty or undecodable; always exits 0 (staged.R reads "null").
x <- commandArgs(trailingOnly = TRUE)[1]
out <- tryCatch({
  if (is.na(x) || !nzchar(x)) stop("empty")
  b <- chartr("-_", "+/", x); b <- paste0(b, strrep("=", (4 - nchar(b) %% 4) %% 4))
  j <- jsonlite::fromJSON(rawToChar(memDecompress(jsonlite::base64_dec(b), "gzip")))
  format(as.POSIXct(j$time, origin = "1970-01-01", tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
}, error = function(e) "null")
cat(out, "\n", sep = "")
