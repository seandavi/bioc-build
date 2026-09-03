#!/usr/bin/env Rscript
# Installs the DESCRIPTION dependency closure via BiocManager::install(),
# then appends one deps_resolved event line to the events file.
# args: <DESCRIPTION path> <events.ndjson path> <package> <stream>
args <- commandArgs(TRUE)
desc_path <- args[1]; events_path <- args[2]; pkg <- args[3]; stream <- args[4]

options(repos = BiocManager::repositories())

parse_field <- function(field) {
  if (is.na(field) || !nzchar(field)) return(character(0))
  parts <- strsplit(field, ",")[[1]]
  parts <- trimws(gsub("\\(.*\\)", "", parts))
  parts[nzchar(parts) & parts != "R"]
}

strong <- c("Depends", "Imports", "LinkingTo")
desc <- read.dcf(desc_path)[1, ]
direct_strong <- unique(unlist(lapply(strong, function(f) parse_field(desc[f]))))
direct_suggests <- parse_field(desc["Suggests"])
direct <- unique(c(direct_strong, direct_suggests))

ap <- available.packages()
# remotes may not be installed; tools::package_dependencies over the
# available.packages() db is enough to get the transitive closure.
#
# Suggests are taken one level deep only (recursed for their own *strong*
# deps, so they're actually loadable, but not for their own Suggests).
# Recursing Suggests at every depth (which="most") reaches unrelated,
# sometimes-unbuildable optional packages several hops down -- e.g. Rcplex,
# a commercial-solver binding, showed up this way under a mass-spec package
# and broke the whole install. This matches Bioconductor's own build system,
# which doesn't chase Suggests-of-Suggests either.
closure <- unique(c(direct, unlist(tools::package_dependencies(
  direct, db = ap, which = strong, recursive = TRUE
))))
closure <- setdiff(closure, rownames(installed.packages()))
closure <- closure[closure %in% rownames(ap)]  # drop anything not in a resolvable repo

if (length(closure)) {
  BiocManager::install(closure, update = FALSE, ask = FALSE)
}
if (!requireNamespace("BiocCheck", quietly = TRUE)) {
  BiocManager::install("BiocCheck", update = FALSE, ask = FALSE)
}

missing <- setdiff(closure, rownames(installed.packages()))
if (length(missing)) {
  stop("failed to install: ", paste(missing, collapse = ", "))
}

ip_all <- installed.packages()
want <- intersect(union(direct, closure), rownames(ip_all))
ip <- ip_all[want, c("Package", "Version"), drop = FALSE]
payload <- list(
  ts = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
  event = "deps_resolved", package = pkg, stream = stream,
  installed = setNames(as.list(ip[, "Version"]), ip[, "Package"])
)
cat(jsonlite::toJSON(payload, auto_unbox = TRUE), "\n", file = events_path, append = TRUE, sep = "")
