#!/usr/bin/env Rscript
# Installs the DESCRIPTION dependency closure via install.packages(), then
# appends one deps_resolved event line to the events file.
# args: <DESCRIPTION path> <events.ndjson path> <package> <stream> <universe>
#
# No BiocManager here (never touches bioconductor.org, per repo policy):
# Bioconductor software deps come from bioc-registry's own served repo
# instead (the phase-2 unified-repo goal, done early) and everything else
# from CRAN via P3M. bioc-registry serves source only for linux, so heavy
# dependency trees now build from source instead of installing binaries.
args <- commandArgs(TRUE)
desc_path <- args[1]; events_path <- args[2]; pkg <- args[3]; stream <- args[4]; universe <- args[5]

codename <- sub('^VERSION_CODENAME="?([^"]*)"?$', '\\1',
                grep("^VERSION_CODENAME=", readLines("/etc/os-release"), value = TRUE))
options(repos = c(
  BiocRegistry = paste0("https://bioc-registry.seandavi.workers.dev/repo/", universe),
  CRAN = paste0("https://p3m.dev/cran/__linux__/", codename, "/latest")
))

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
# Suggests are taken one level deep only (recursed for their own *strong*
# deps, so they're actually loadable, but not for their own Suggests).
# Recursing Suggests at every depth (which="most") reaches unrelated,
# sometimes-unbuildable optional packages several hops down -- e.g. Rcplex,
# a commercial-solver binding, showed up this way under a mass-spec package
# and broke the whole install. This matches Bioconductor's own build system,
# which doesn't chase Suggests-of-Suggests either.
#
# BiocStyle is added unconditionally: it's the de facto vignette-styling
# package across Bioconductor, but plenty of packages (rnaseqDTU included)
# use it in their .Rmd without declaring it in DESCRIPTION at all.
closure <- unique(c(direct, "BiocStyle", "BiocCheck", unlist(tools::package_dependencies(
  direct, db = ap, which = strong, recursive = TRUE
))))
closure <- setdiff(closure, rownames(installed.packages()))
closure <- closure[closure %in% rownames(ap)]  # drop anything not in a resolvable repo

if (length(closure)) {
  install.packages(closure)
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
