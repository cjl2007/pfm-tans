function tans_smoke_config_test()
%TANS_SMOKE_CONFIG_TEST Lightweight smoke test for reorganized config pathing.

tans_add_repo_paths();
cfg = tans_config_template_blank(pwd);

assert(isfield(cfg, 'target'), 'Missing cfg.target');
assert(isfield(cfg.target, 'maxCandidateTargets'), 'Missing cfg.target.maxCandidateTargets');
assert(cfg.target.maxCandidateTargets == 1, 'Default maxCandidateTargets should be 1.');
assert(isfield(cfg, 'paths'), 'Missing cfg.paths');
assert(isfield(cfg.paths, 'tansRoot'), 'Missing cfg.paths.tansRoot');

disp('tans_smoke_config_test passed');
end
