function cfg = tans_config_bd2_salience(Subdir)
%TANS_CONFIG_BD2_SALIENCE Example salience-network targeting config.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end
[~, Subject] = fileparts(Subdir);

cfg = tans_workflow_defaults(Subdir);

repoDir = tans_add_repo_paths();

cfg.paths.resourcesRoot = fullfile(repoDir, 'res0urces');
cfg.paths.mscRoot = '/path/to/msc';
cfg.paths.simnibsRoot = '/path/to/SimNIBS';
cfg.paths.tansRoot = repoDir;
cfg.paths.searchSpace = fullfile(repoDir, 'res0urces', 'LPFC_LH.dtseries.nii');

cfg.inputs.probMapsFile = fullfile(Subdir, 'func', 'rest', 'PFM', 'RidgeFusion_VTX_ProbMaps.dtseries.nii');

cfg.tolerability.dataFile = fullfile(repoDir, 'res0urces', 'meteyard-l_holmes-2018_TMS-SMART_data.txt');
cfg.tolerability.eegPositionsFile = fullfile(Subdir, 'tans', 'HeadModel', ['m2m_' Subject], 'eeg_positions', 'EEG10-20_extended_SPM12.csv');

cfg.target.name = 'Salience-LH';
cfg.target.networkColumn = 13;
cfg.target.offTargetColumn = 14;
cfg.target.useAvoidance = true;
cfg.target.maxCandidateTargets = 1;

cfg.export.brainsightFileName = 'Salience-LH_OptimalTrajectory_BrainSight.txt';
cfg.export.pythonExe = fullfile(cfg.paths.simnibsRoot, 'simnibs_env', 'bin', 'python3.11');
end
