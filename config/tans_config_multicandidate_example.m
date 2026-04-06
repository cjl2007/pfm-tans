function cfg = tans_config_multicandidate_example(Subdir)
%TANS_CONFIG_MULTICANDIDATE_EXAMPLE Example config enabling backup targets.

cfg = tans_config_template_blank(Subdir);

% Fill the machine-specific and dataset-specific paths below.
cfg.paths.resourcesRoot = '/path/to/resources';
cfg.paths.mscRoot = '/path/to/msc';
cfg.paths.simnibsRoot = '/path/to/SimNIBS';
cfg.paths.searchSpace = '/path/to/search_space.dtseries.nii';
cfg.inputs.probMapsFile = fullfile(Subdir, 'func', 'rest', 'PFM', 'RidgeFusion_VTX_ProbMaps.dtseries.nii');
cfg.tolerability.dataFile = '/path/to/meteyard-l_holmes-2018_TMS-SMART_data.txt';
cfg.tolerability.eegPositionsFile = fullfile(Subdir, 'tans', 'HeadModel', 'm2m_SUBJECT', 'eeg_positions', 'EEG10-20_extended_SPM12.csv');

cfg.target.name = 'Salience-LH';
cfg.target.networkColumn = 13;
cfg.target.offTargetColumn = 14;
cfg.target.useAvoidance = true;
cfg.target.maxCandidateTargets = 3;
end
