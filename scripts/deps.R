#!/usr/bin/env Rscript
# Installs the DESCRIPTION's direct dependencies via a single install.packages()
# call, letting it resolve transitive hard deps itself -- same shape as
# r-universe's getdeps.R (github.com/r-universe-org/actions/blob/HEAD/getdeps.R),
# not a hand-rolled closure. Then appends one deps_resolved event line.
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

desc <- as.data.frame(read.dcf(desc_path))
fields <- c("Depends", "Imports", "LinkingTo", "Suggests", "Enhances", "VignetteBuilder")
raw <- unlist(desc[intersect(fields, names(desc))])
pkg_deps <- unique(trimws(sub("\\(.*\\)", "", unlist(strsplit(as.character(raw), ",")))))
skiplist <- c("R", row.names(installed.packages(priority = "base")))
pkg_deps <- setdiff(pkg_deps, skiplist)

# BiocStyle/BiocCheck are ours to guarantee regardless of what DESCRIPTION
# declares: BiocStyle in particular is routinely used straight in a
# vignette's YAML header without ever being added to Suggests (rnaseqDTU
# does exactly this) -- no DESCRIPTION-driven dependency list, r-universe's
# included, would catch that.
pkg_deps <- union(pkg_deps, c("BiocStyle", "BiocCheck"))

if (length(pkg_deps)) install.packages(pkg_deps)

missing <- setdiff(pkg_deps, rownames(installed.packages()))
if (length(missing)) {
  stop("failed to install direct dependencies: ", paste(missing, collapse = ", "))
}

ip_all <- installed.packages()
ip <- ip_all[intersect(pkg_deps, rownames(ip_all)), c("Package", "Version"), drop = FALSE]
payload <- list(
  ts = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
  event = "deps_resolved", package = pkg, stream = stream,
  installed = setNames(as.list(ip[, "Version"]), ip[, "Package"])
)
cat(jsonlite::toJSON(payload, auto_unbox = TRUE), "\n", file = events_path, append = TRUE, sep = "")
