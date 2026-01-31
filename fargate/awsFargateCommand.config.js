exports.cluster_arn = "YOUR_CLUSTER_ARN";
exports.subnet_1 = "YOUR_SUBNET_ARN";
exports.metrics = true;

exports.options = {
  bucket: "S3_BUCKET_NAME",
  prefix: "hyperflow",
};

exports.tasks_mapping = {
  default: "WORKER_TASK_DEFINITION_ARN",
  dataProvider: "DATA_PROVIDER_TASK_DEFINITION_ARN",
};
