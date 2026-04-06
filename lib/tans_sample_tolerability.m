function result = tans_sample_tolerability(candidateOutDir, model, coilCenterVertex)
%TANS_SAMPLE_TOLERABILITY Sample a dense tolerability map at the final site.

tolerabilityDir = fullfile(candidateOutDir, 'Tolerability');
if ~exist(tolerabilityDir, 'dir')
    mkdir(tolerabilityDir);
end

assert(coilCenterVertex >= 1 && coilCenterVertex <= size(model.vertexCoords, 1), ...
    'coilCenterVertex is outside the scalp vertex domain.');

result = struct;
result.vertex_index = coilCenterVertex;
result.sampled_coordinate_native_mm = model.vertexCoordsNative(coilCenterVertex, :);
result.sampled_coordinate_eval_space_mm = model.vertexCoords(coilCenterVertex, :);
result.coordinate_space = model.evalCoordinateSpace;
result.source_data_coordinate_space = model.data.coordinateSpace;
result.nearest_source_distance_mm = model.nearestSourceDistanceMM(coilCenterVertex);
result.max_extrapolation_distance_mm = model.maxExtrapolationDistanceMM;
result.is_within_valid_domain = model.validDomainMask(coilCenterVertex);
result.status = 'ok';
result.error_message = '';
result.metrics = struct;

for i = 1:numel(model.metricNames)
    metricName = model.metricNames{i};
    result.metrics.(metricName) = model.metricMap.(metricName)(coilCenterVertex);
end

if ~result.is_within_valid_domain
    result.status = 'outside_valid_domain';
    result.error_message = sprintf(['Final coil-center vertex lies outside the tolerability ', ...
        'interpolation domain (nearest source distance %.3f mm, limit %.3f mm).'], ...
        result.nearest_source_distance_mm, result.max_extrapolation_distance_mm);
end

tans_write_struct_txt(fullfile(tolerabilityDir, 'TolerabilitySample.txt'), result);
save(fullfile(tolerabilityDir, 'TolerabilitySample.mat'), 'result');

assert(result.is_within_valid_domain, '%s', result.error_message);
end
