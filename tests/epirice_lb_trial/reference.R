# Execute the pinned upstream functions without rewriting their SEIR updates.
# Copyright notices and GPL-3 license are retained in downloaded original files.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("Usage: Rscript reference.R <trial-output-folder>")
root <- normalizePath(args[[1]], mustWork = TRUE)
library(data.table)
source(file.path(root, "upstream/R/calculate_audpc.R"))
source(file.path(root, "upstream/R/SEIR.R"))
source(file.path(root, "upstream/R/predict_leaf_blast.R"))

# Read local vectors on exit. This exposes intermediate outputs but does not
# replace, insert, or reorder any statement in the upstream SEIR loop.
invisible(capture.output(trace("SEIR", where = .GlobalEnv, print = FALSE,
  exit = quote({
    assign(".trial_trace", data.frame(
      simday = seq_len(duration), date = as.character(dates[seq_len(duration)]),
      RcOpt = RcOpt, Rc = rc, RcA = Rc_age[seq_len(duration)], RcT = Rc_temp, RcW = RcW,
      healthy_sites = sites, latent = now_latent, infectious = now_infectious,
      removed = removed, rateinf_new_infections = infection,
      growth_new_sites = rgrowth, senescence_sites_rate = rsenesced,
      healthy_fraction_C = cofr, intensity_active = intensity,
      AUDPC = rep_len(AUDPC, duration),
      AUDPC_cumulative = c(0, cumsum((intensity[-duration] + intensity[-1]) / 2)),
      total_sites = total_sites, senesced_total = senesced,
      rtransfer_L_to_I = rtransfer, diseased_total = diseased,
      latent_cohort_entries = latency, infectious_cohort_entries = infectious
    ), envir = .GlobalEnv)
  })
)))
dir.create(file.path(root, "R"), showWarnings = FALSE)
for (case in c("constant_wet", "boundary_cycle", "constant_dry")) {
  wth <- fread(file.path(root, "inputs", paste0(case, ".csv")))
  wth[, YYYYMMDD := as.Date(YYYYMMDD)]
  original <- predict_leaf_blast(wth, emergence = as.character(wth$YYYYMMDD[[1]]))
  # Ensure tracing reflects the values returned by the unmodified model.
  stopifnot(identical(.trial_trace$healthy_sites, original$sites))
  stopifnot(identical(.trial_trace$rateinf_new_infections, original$rateinf))
  stopifnot(identical(.trial_trace$intensity_active, original$intensity))
  stopifnot(identical(.trial_trace$AUDPC, original$AUDPC))
  write.csv(.trial_trace, file.path(root, "R", paste0(case, ".csv")), row.names = FALSE)
}
untrace("SEIR", where = .GlobalEnv)
writeLines(capture.output(sessionInfo()), file.path(root, "R/session_info.txt"))
cat("Executed original epicrop v0.0.2 R helper for three complete 120-day cases.\n")
