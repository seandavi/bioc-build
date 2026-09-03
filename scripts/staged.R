#!/usr/bin/env Rscript
# The one place staged.json gets written, called from build.sh on every exit
# path (success or failed:<stage>) so the shape in SPEC-014 is never
# duplicated. Positional args, "null"/"none" standing in for absent values
# since bash has no native null.
a <- commandArgs(TRUE)
g <- function(i) if (a[i] %in% c("null", "")) NA else a[i]
status        <- a[1]
package       <- a[2]
version       <- g(3)
stream        <- a[4]
tarball_file  <- g(5)
sha256        <- g(6)
size_bytes    <- suppressWarnings(as.numeric(g(7)))
git_url       <- g(8)
branch        <- g(9)
commit        <- g(10)
manifest_commit <- g(11)
policy_version  <- g(12)
run_id        <- a[13]
run_attempt   <- suppressWarnings(as.integer(a[14]))
run_url       <- a[15]
container     <- g(16)
r_version     <- g(17)
check_status  <- g(18)
bioccheck     <- g(19)
desc_path     <- a[20]
out_path      <- a[21]

null_or <- function(x) if (is.na(x)) NULL else jsonlite::unbox(x)

desc_fields <- c("Depends", "Imports", "Suggests", "License", "NeedsCompilation",
                  "Priority", "LinkingTo", "Enhances", "OS_type")
meta_fields <- c("Title", "Description", "URL", "BugReports", "Maintainer",
                  "Author", "biocViews")

description <- setNames(as.list(rep(NA_character_, length(desc_fields))), desc_fields)
meta <- setNames(as.list(rep(NA_character_, length(meta_fields))), meta_fields)
if (!is.na(desc_path) && desc_path != "none" && file.exists(desc_path)) {
  d <- read.dcf(desc_path)[1, ]
  for (f in desc_fields) if (!is.na(d[f])) description[[f]] <- unname(d[f])
  for (f in meta_fields) if (!is.na(d[f])) meta[[f]] <- unname(d[f])
}
description <- lapply(description, function(x) if (is.na(x)) NULL else x)
meta <- lapply(meta, function(x) if (is.na(x)) NULL else x)

staged <- list(
  schema_version = jsonlite::unbox("1"),
  package = jsonlite::unbox(package),
  version = null_or(version),
  stream = jsonlite::unbox(stream),
  status = jsonlite::unbox(status),
  tarball = list(
    file = null_or(tarball_file),
    sha256 = null_or(sha256),
    size_bytes = if (is.na(size_bytes)) NULL else jsonlite::unbox(size_bytes)
  ),
  source = list(
    git_url = null_or(git_url),
    branch = null_or(branch),
    commit = null_or(commit)
  ),
  manifest_commit = null_or(manifest_commit),
  policy_version = null_or(policy_version),
  build = list(
    run_id = jsonlite::unbox(run_id),
    run_attempt = if (is.na(run_attempt)) NULL else jsonlite::unbox(run_attempt),
    run_url = jsonlite::unbox(run_url),
    container = null_or(container),
    r_version = null_or(r_version)
  ),
  check = list(
    status = null_or(check_status),
    bioccheck = null_or(bioccheck)
  ),
  description = description,
  meta = meta
)

writeLines(jsonlite::toJSON(staged, auto_unbox = TRUE, null = "null", pretty = TRUE), out_path)
