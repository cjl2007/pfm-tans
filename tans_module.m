function report = tans_module(Subdir, configFile)
%TANS_MODULE Preflight/validation entrypoint for PFM-TANS workflows.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end
if nargin < 2 || isempty(configFile)
    error('Usage: tans_module(Subdir, configFile)');
end

tans_add_repo_paths();
cfg = tans_load_config(Subdir, configFile);
cfg = i_apply_defaults(cfg);
report = tans_preflight_check(Subdir, cfg, 'Stage', 'preheadmodel', ...
    'ErrorIfMissing', true, 'Verbose', true);
end

function cfg = i_apply_defaults(cfg)
if ~isfield(cfg, 'headmodel'); cfg.headmodel = struct; end
if ~isfield(cfg.headmodel, 'skipExistingHeadModel') || isempty(cfg.headmodel.skipExistingHeadModel)
    cfg.headmodel.skipExistingHeadModel = true;
end
if ~isfield(cfg.headmodel, 'overwriteExistingHeadModel') || isempty(cfg.headmodel.overwriteExistingHeadModel)
    cfg.headmodel.overwriteExistingHeadModel = false;
end
if ~isfield(cfg, 'target'); cfg.target = struct; end
if ~isfield(cfg.target, 'useAvoidance') || isempty(cfg.target.useAvoidance)
    cfg.target.useAvoidance = true;
end
if ~isfield(cfg.target, 'maxCandidateTargets') || isempty(cfg.target.maxCandidateTargets)
    cfg.target.maxCandidateTargets = 1;
end
end
