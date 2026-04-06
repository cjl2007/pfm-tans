function cfg = tans_dose_config_template_blank(Subdir)
% Blank template for PFM-TANS dose workflow configuration.
% Fill all required fields before use with tans_dose_workflow.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end
[~, Subject] = fileparts(Subdir);
repoDir = tans_add_repo_paths();

cfg = struct;
cfg.Subdir = Subdir;

% Tool/resource roots (REQUIRED)
cfg.paths.resourcesRoot = '';
cfg.paths.mscRoot = '';
cfg.paths.simnibsRoot = '';
cfg.paths.tansRoot = repoDir;

% Input files (REQUIRED unless noted)
cfg.inputs.probMapsFile = '';
cfg.inputs.targetMapFile = '';      % optional direct target map; if set, overrides probMaps+networkColumn
cfg.inputs.offTargetMapFile = '';   % optional direct off-target map; if set, overrides probMaps+offTargetColumn
cfg.inputs.functionalNetworksFile = '';
cfg.inputs.networkLabelsFile = '';  % optional if networkPriorsFile provided
cfg.inputs.networkPriorsFile = '';  % preferred for network color scheme in dlabel output
cfg.inputs.optimizedMagnEFile = fullfile(Subdir, 'tans', 'Target', 'Optimize', ...
    'magnE_BestCoilCenter+BestOrientation.dtseries.nii');
cfg.inputs.rmtBrainsightFile = ''; % optional; if empty -> <Subdir>/tans/rMT/rMT_Trajectory_BrainSight.txt
cfg.inputs.rmtDidtFile = '';       % optional; if empty and didtAperUs empty -> <Subdir>/tans/rMT/DiDt.txt
cfg.inputs.priorsFile = ''; % optional: set '' to disable weighted prior mask
cfg.inputs.gyralLabelsFile = fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', ...
    sprintf('%s.aparc.32k_fs_LR.dlabel.nii', Subject)); % optional override

% Target definition
cfg.target.name = 'Target';
cfg.target.networkColumn = 0;
cfg.target.offTargetColumn = 0;
cfg.target.useAvoidance = true;
cfg.target.mapMode = 'auto';          % {'auto','binary','probabilistic'}
cfg.target.mapScalePercentile = 99;   % used for probabilistic normalization

% SimNIBS
cfg.simnibs.coilRelativePath = '';

% rMT calibration
cfg.rmt.didtAperUs = [];           % optional scalar override; if empty, read from rmtDidtFile/default DiDt.txt
cfg.rmt.precentralLabelValue = 24;   % e.g., LH precentral in aparc dlabel
cfg.rmt.priorColumn = 16;            % column in Priors.Spatial
cfg.rmt.priorPercentile = 90;
cfg.rmt.thresholdMethod = 'weighted_quantile'; % {'percentile','weighted_quantile'}
cfg.rmt.thresholdQuantile = 0.99;
cfg.rmt.weightGamma = 2;
cfg.rmt.skipExistingSimulation = true;
cfg.rmt.brainsightSampleIndex = 1;
cfg.rmt.brainsightWhich = 'samples';
cfg.rmt.brainsightUseSimnibsPython = true;

% Dose optimization
cfg.dose.didtRangeAperUs = 40:2:90;
cfg.dose.minHotSpotSizeMM2 = 5;
cfg.dose.outDirName = 'Dose';
cfg.dose.selectionMetric = 'on_target'; % {'on_target','penalized'}
cfg.dose.referenceDiDtAperUs = 1;       % dI/dt represented by input optimized magnE map

end
