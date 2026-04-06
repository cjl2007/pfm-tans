function cfg = tans_load_config(Subdir, configFile)
%TANS_LOAD_CONFIG Resolve and execute a PFM-TANS config.

tans_add_repo_paths();

if isstring(configFile)
    configFile = char(configFile);
end

if isstruct(configFile)
    cfg = configFile;
    return;
end

if ~ischar(configFile)
    error('configFile must be a config function name, path to .m file, or struct.');
end

[p, n, e] = fileparts(configFile);
if ~isempty(p) || ~isempty(e)
    if isempty(e)
        e = '.m';
    end
    cfgPath = fullfile(p, [n e]);
    assert(exist(cfgPath, 'file') == 2, 'Config file not found: %s', cfgPath);
    if ~isempty(p)
        addpath(p);
    end
    cfg = feval(n, Subdir);
else
    cfg = feval(configFile, Subdir);
end
end
