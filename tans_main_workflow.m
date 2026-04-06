function outputs = tans_main_workflow(Subdir, configFile)
%TANS_MAIN_WORKFLOW Compatibility wrapper for the canonical workflow.

tans_add_repo_paths();
outputs = tans_main_workflow_impl(Subdir, configFile);
end
