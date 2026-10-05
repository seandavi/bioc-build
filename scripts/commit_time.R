#!/usr/bin/env Rscript
# Decode build-source's COMMITINFO (buildtools::commit_info_base64: url-safe
# base64 without padding, zlib stream, JSON with `time` in epoch seconds)
# and print the commit time as %Y-%m-%dT%H:%M:%SZ (UTC). Prints `null` if the
# argument is empty or undecodable; always exits 0 (staged.R reads "null").
# Base R only: the linux job's R library does not necessarily have jsonlite
# (it was missing for ALL, run 37388670427).
b64decode <- function(s) {
  tbl <- c(LETTERS, letters, 0:9, "+", "/")
  v <- match(strsplit(sub("=+$", "", chartr("-_", "+/", s)), "")[[1]], tbl) - 1L
  if (anyNA(v) || !length(v)) stop("bad base64")
  bits <- unlist(lapply(v, function(x) rev(as.integer(intToBits(x))[1:6])))
  bits <- bits[seq_len((length(bits) %/% 8) * 8)]
  as.raw(colSums(matrix(bits, 8) * 2^(7:0)))
}
x <- commandArgs(trailingOnly = TRUE)[1]
out <- tryCatch({
  if (is.na(x) || !nzchar(x)) stop("empty")
  j <- rawToChar(memDecompress(b64decode(x), "gzip"))
  t <- as.numeric(sub('.*"time"[[:space:]]*:[[:space:]]*([0-9.]+).*', "\\1", j))
  if (is.na(t)) stop("no time")
  format(as.POSIXct(t, origin = "1970-01-01", tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ")
}, error = function(e) "null", warning = function(w) "null")
cat(out, "\n", sep = "")
