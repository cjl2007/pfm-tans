function outputs = tans_run_dose(Subdir, configFile)
%TANS_RUN_DOSE User-facing launcher for the dose workflow.

tans_add_repo_paths();
outputs = tans_dose_workflow(Subdir, configFile);
end
