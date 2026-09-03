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

desc <- read.dcf(desc_path)[1, ]
direct <- unique(unlist(lapply(c("Depends", "Imports", "LinkingTo", "Suggests"),
                                function(f) parse_field(desc[f]))))

ap <- available.packages()
# remotes may not be installed; tools::package_dependencies over the
# available.packages() db is enough to get the transitive closure.
closure <- unique(c(direct, unlist(tools::package_dependencies(
  direct, db = ap, which = "most", recursive = TRUE
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
