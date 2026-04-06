function provenance = tans_write_runtime_provenance(runDir, cfg, varargin)
%TANS_WRITE_RUNTIME_PROVENANCE Save resolved config and runtime metadata.

p = inputParser;
addParameter(p, 'Stage', 'workflow', @(x)ischar(x) || isstring(x));
addParameter(p, 'Extra', struct, @isstruct);
parse(p, varargin{:});

stageName = char(string(p.Results.Stage));
extra = p.Results.Extra;

if ~exist(runDir, 'dir')
    mkdir(runDir);
end

provenance = struct;
provenance.stage = stageName;
provenance.timestamp = datestr(now, 30);
provenance.matlab = version;
provenance.cfg = cfg;
provenance.extra = extra;
provenance.software = i_collect_software_versions(cfg);

save(fullfile(runDir, 'ResolvedConfig.mat'), 'cfg', 'provenance');
tans_write_struct_txt(fullfile(runDir, 'ResolvedConfig.txt'), cfg);
tans_write_struct_txt(fullfile(runDir, 'RunMetadata.txt'), provenance);
end

function software = i_collect_software_versions(cfg)
software = struct;
software.wb_command = i_try_command('wb_command -version');
software.charm = i_try_command('charm --version');
software.fslmaths = i_try_command('fslmaths -version');
software.matlab = version;

if isfield(cfg, 'export') && isfield(cfg.export, 'pythonExe') && ~isempty(cfg.export.pythonExe)
    software.python = i_try_command(sprintf('"%s" --version', cfg.export.pythonExe));
else
    software.python = i_try_command('python3 --version');
end
end

function out = i_try_command(cmd)
[status, txt] = system(cmd);
if status == 0
    out = strtrim(txt);
else
    out = sprintf('unavailable (%s)', strtrim(cmd));
end
end
