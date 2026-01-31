import { ECSClient } from "@aws-sdk/client-ecs";
import { EFSClient } from "@aws-sdk/client-efs";
import { EC2Client } from "@aws-sdk/client-ec2";
import { LambdaClient } from "@aws-sdk/client-lambda";
import { S3Client } from "@aws-sdk/client-s3";
import { hideBin } from "yargs/helpers";
import yargs from "yargs";

import { createVPCAndSubnets } from "./vpcUtils.js";
import { createEFSFileSystem, sleep } from "./efsUtils.js";
import { createTaskDefinitions, setupCluster } from "./ecsUtils.js";
import {
    createS3Bucket,
    runDataSyncTask,
    syncImageData,
    createLambdaFunction,
} from "./dataUtils.js";

const REGION = "us-east-1";

async function main(args) {
    const ecsClient = new ECSClient({ region: REGION });
    const ec2Client = new EC2Client({ region: REGION });
    const efsClient = new EFSClient({ region: REGION });
    const s3Client = new S3Client({ region: REGION });
    const lambdaClient = new LambdaClient({ region: REGION });

    const vpcData = await createVPCAndSubnets(ec2Client);
    if (vpcData.length === 0) {
        console.error("Error creating VPC and subnets");
        return;
    }
    // const securityGroupId = await createSecurityGroup(ec2Client, vpcData);
    const securityGroupId = vpcData.securityGroup.GroupId;
    const clusterData = await setupCluster(ecsClient);
    const efsData = await createEFSFileSystem(
        efsClient,
        vpcData,
        securityGroupId,
    );
    const taskDefinitions = await createTaskDefinitions(ecsClient, efsData, args.roleArn, args.workerImage, args.dataImage);
    console.log("Task definitions created successfully:", taskDefinitions);
    const s3BucketData = await createS3Bucket(s3Client);
    console.log("S3 bucket name:", s3BucketData.Location);
    await createLambdaFunction(lambdaClient, args.roleArn);

    if (args.dataImage) {
        console.log("Running data sync, can take up to a few minutes...");
        await syncImageData(args.dataImage, lambdaClient);
        await sleep(60000);
        await runDataSyncTask(
            ecsClient,
            clusterData.clusterArn,
            vpcData.subnets[0].SubnetId,
            taskDefinitions.dataProvider.taskDefinitionArn,
        );
        console.log("Data sync complete");
    }

    console.log(
        "AWSFargateCommandConfig: \n",
        `
    exports.cluster_arn = ${clusterData.clusterArn};
    exports.subnet_1 = ${vpcData.subnets[0].SubnetId};
    exports.metrics = true;

    exports.options = {
        "bucket": ${s3BucketData.Location},
        "prefix": "hyperflow",
    };

    exports.tasks_mapping = {
        "default": "${taskDefinitions.worker.taskDefinitionArn}",
        "dataProvider": "${taskDefinitions.worker.taskDefinitionArn}",
    };
    `,
    );
}

const argv = yargs(hideBin(process.argv))
    .option("workerImage", {
        alias: "w",
        description: "Image for worker task",
        type: "string",
        demandOption: true,
    })
    .option("dataImage", {
        alias: "d",
        description: "Image containing data for workflow",
        type: "string",
        demandOption: true,
    })
    .option("roleArn", {
        alias: "r",
        description: "Role ARN for task execution",
        type: "string",
        demandOption: true,
    })
    .help()
    .alias("help", "h").argv;

main(argv);
