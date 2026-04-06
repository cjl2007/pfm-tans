function tans_write_struct_txt(filePath, S)
%TANS_WRITE_STRUCT_TXT Write a nested struct to a readable text file.

fid = fopen(filePath, 'w');
assert(fid > 0, 'Unable to open file for writing: %s', filePath);
cleanupObj = onCleanup(@() fclose(fid)); %#ok<NASGU>

i_write_struct(fid, S, '');
end

function i_write_struct(fid, value, prefix)
if isstruct(value)
    fields = fieldnames(value);
    for i = 1:numel(fields)
        name = fields{i};
        child = value.(name);
        nextPrefix = name;
        if ~isempty(prefix)
            nextPrefix = [prefix '.' name];
        end
        i_write_struct(fid, child, nextPrefix);
    end
    return;
end

fprintf(fid, '%s = %s\n', prefix, i_value_to_string(value));
end

function out = i_value_to_string(value)
if ischar(value)
    out = value;
elseif isstring(value)
    out = char(join(value, ', '));
elseif islogical(value) && isscalar(value)
    out = mat2str(value);
elseif isnumeric(value)
    out = mat2str(value);
elseif iscell(value)
    parts = cell(size(value));
    for i = 1:numel(value)
        parts{i} = i_value_to_string(value{i});
    end
    out = ['{' strjoin(parts, ', ') '}'];
else
    out = class(value);
end
end
