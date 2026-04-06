function tans_smoke_config_test()
%TANS_SMOKE_CONFIG_TEST Lightweight smoke test for reorganized config pathing.

tans_add_repo_paths();
cfg = tans_config_template_blank(pwd);

assert(isfield(cfg, 'target'), 'Missing cfg.target');
assert(isfield(cfg.target, 'maxCandidateTargets'), 'Missing cfg.target.maxCandidateTargets');
assert(cfg.target.maxCandidateTargets == 1, 'Default maxCandidateTargets should be 1.');
assert(isfield(cfg, 'paths'), 'Missing cfg.paths');
assert(isfield(cfg.paths, 'tansRoot'), 'Missing cfg.paths.tansRoot');

tans_preflight_stage_test();

disp('tans_smoke_config_test passed');
end

function tans_preflight_stage_test()
%TANS_PREFLIGHT_STAGE_TEST Ensure generated head-model files are stage-gated.

subdir = tempname;
mkdir(subdir);
mkdir(fullfile(subdir, 'anat', 'T1w', 'fsaverage_LR32k'));
mkdir(fullfile(subdir, 'anat', 'T2w'));
mkdir(fullfile(subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k'));
mkdir(fullfile(subdir, 'tans'));

[~, subject] = fileparts(subdir);
cfg = i_test_cfg(subdir, subject);

pre = tans_preflight_check(subdir, cfg, 'Stage', 'preheadmodel', ...
    'ErrorIfMissing', false, 'Verbose', false);
assert(pre.Passed, 'Pre-headmodel preflight should ignore generated head-model files.');

post = tans_preflight_check(subdir, cfg, 'Stage', 'postheadmodel', ...
    'ErrorIfMissing', false, 'Verbose', false);
assert(~post.Passed, 'Post-headmodel preflight should require generated head-model files.');

labels = string(post.Missing(:, 1));
assert(any(labels == "Tolerability EEG positions file"), ...
    'Post-headmodel preflight should require subject EEG positions.');

disp('tans_preflight_stage_test passed');
end

function cfg = i_test_cfg(subdir, subject)
touches = {
    fullfile(subdir, 'probmaps.dscalar.nii')
    fullfile(subdir, 'searchspace.dtseries.nii')
    fullfile(subdir, 'tolerability.tsv')
    fullfile(subdir, 'anat', 'T1w', 'T1w_acpc.nii.gz')
    fullfile(subdir, 'anat', 'T2w', 'T2w_acpc.nii.gz')
    fullfile(subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.L.midthickness.32k_fs_LR.surf.gii', subject))
    fullfile(subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.L.white.32k_fs_LR.surf.gii', subject))
    fullfile(subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.L.pial.32k_fs_LR.surf.gii', subject))
    fullfile(subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.R.midthickness.32k_fs_LR.surf.gii', subject))
    fullfile(subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.R.white.32k_fs_LR.surf.gii', subject))
    fullfile(subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.R.pial.32k_fs_LR.surf.gii', subject))
    fullfile(subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.midthickness_va.32k_fs_LR.dscalar.nii', subject))
    fullfile(subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.sulc.32k_fs_LR.dscalar.nii', subject))
    fullfile(subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.L.atlasroi.32k_fs_LR.shape.gii', subject))
    fullfile(subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.R.atlasroi.32k_fs_LR.shape.gii', subject))
    fullfile(subdir, 'coil.ccd')
    };
for i = 1:numel(touches)
    fid = fopen(touches{i}, 'w');
    assert(fid > 0, 'Unable to create test file: %s', touches{i});
    fclose(fid);
end

cfg = struct;
cfg.inputs = struct('probMapsFile', fullfile(subdir, 'probmaps.dscalar.nii'));
cfg.paths = struct( ...
    'searchSpace', fullfile(subdir, 'searchspace.dtseries.nii'), ...
    'simnibsRoot', subdir);
cfg.tolerability = struct( ...
    'dataFile', fullfile(subdir, 'tolerability.tsv'), ...
    'eegPositionsFile', fullfile(subdir, 'tans', 'HeadModel', ['m2m_' subject], ...
        'eeg_positions', 'EEG10-20_extended_SPM12.csv'));
cfg.headmodel = struct( ...
    't1File', 'T1w_acpc.nii.gz', ...
    't2File', 'T2w_acpc.nii.gz', ...
    'hemispheres', {{'L', 'R'}}, ...
    'surfaceTypes', {{'midthickness', 'white', 'pial'}});
cfg.simnibs = struct('coilRelativePath', 'coil.ccd');
cfg.export = struct('writeBrainsightTxt', false);
end
