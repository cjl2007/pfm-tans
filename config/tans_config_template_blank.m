function cfg = tans_config_template_blank(Subdir)
%TANS_CONFIG_TEMPLATE_BLANK Blank template for a custom targeting config.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end

cfg = tans_workflow_defaults(Subdir);
repoDir = tans_add_repo_paths();

cfg.paths.resourcesRoot = '';
cfg.paths.mscRoot = '';
cfg.paths.simnibsRoot = '';
cfg.paths.tansRoot = repoDir;
cfg.paths.searchSpace = '';

cfg.inputs.probMapsFile = '';

cfg.tolerability.dataFile = '';
cfg.tolerability.eegPositionsFile = fullfile(Subdir, 'tans', 'HeadModel', 'm2m_SUBJECT', 'eeg_positions', 'EEG10-20_extended_SPM12.csv');

cfg.target.name = 'Network-LH';
cfg.target.networkColumn = 0;
cfg.target.offTargetColumn = 0;
cfg.target.useAvoidance = true;
cfg.target.maxCandidateTargets = 1;

cfg.simnibs.coilRelativePath = '';

cfg.export.brainsightFileName = 'OptimalTrajectoryBS.txt';
cfg.export.pythonExe = '';
end
