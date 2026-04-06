%% Example usage for the reorganized PFM-TANS workflow

Subdir = '/path/to/subject';

repoDir = tans_add_repo_paths();
addpath(fullfile(repoDir, 'examples'));

% Validate inputs before expensive computation.
preflight = tans_validate(Subdir, 'tans_config_bd2_salience');
disp(preflight);

% Run the main workflow. Set cfg.target.maxCandidateTargets > 1 in the
% config to generate Candidate1, Candidate2, ... under the target folder.
% Tolerability scoring will use the configured external dataset and
% subject-native EEG positions from the SimNIBS head model.
outputs = tans_run(Subdir, 'tans_config_bd2_salience');
disp(outputs);

% Dose workflow remains available as a separate entrypoint.
doseOutputs = tans_run_dose(Subdir, 'tans_dose_config_bd2_salience');
disp(doseOutputs);
