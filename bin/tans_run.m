function outputs = tans_run(Subdir, configFile)
%TANS_RUN User-facing launcher for the main PFM-TANS workflow.

tans_add_repo_paths();
outputs = tans_main_workflow(Subdir, configFile);
end
