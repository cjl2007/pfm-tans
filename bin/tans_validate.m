function report = tans_validate(Subdir, configFile)
%TANS_VALIDATE User-facing validation/preflight entrypoint.

tans_add_repo_paths();
report = tans_module(Subdir, configFile);
end
