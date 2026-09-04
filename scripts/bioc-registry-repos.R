# bioc-build divergence, loaded via R_PROFILE_USER after base-image's own
# Rprofile.site (R sources both by default): point BioCsoft at bioc-registry
# instead of bioconductor.posit.co. BioCann/BioCexp stay at Posit's mirror
# for now -- annotation packages aren't in bioc-registry and experiment
# packages are what we're building here, so pointing those at ourselves
# would be circular. Phase-2: revisit once bioc-registry serves annotation
# data too.
local({
  universe <- Sys.getenv("UNIVERSE_NAME")
  if (nzchar(universe)) {
    repos <- getOption("repos")
    repos["BioCsoft"] <- paste0("https://bioc-registry.seandavi.workers.dev/repo/", universe)
    options(repos = repos)
  }
})
