import { CreateBucketCommand } from "@aws-sdk/client-s3";
import {
    CreateFunctionCommand,
    InvokeCommand,
    GetFunctionCommand,
} from "@aws-sdk/client-lambda";
import { RunTaskCommand, DescribeTasksCommand } from "@aws-sdk/client-ecs";
import fs from "fs";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function createS3Bucket(s3Client) {
    try {
        const bucketParams = {
            Bucket: `hyperflow-fargate-${Date.now()}`,
        };

        // Create the S3 bucket
        const data = await s3Client.send(new CreateBucketCommand(bucketParams));

        console.log("S3 bucket created successfully:", data);
        return data;
    } catch (err) {
        console.error("Error creating S3 bucket:", err);
    }
}

export async function runDataSyncTask(
    ecsClient,
    clusterArn,
    subnetId,
    taskDefinitionArn,
) {
    try {
        const params = {
            cluster: clusterArn,
            launchType: "FARGATE",
            taskDefinition: taskDefinitionArn,
            count: 1,
            networkConfiguration: {
                awsvpcConfiguration: {
                    subnets: [subnetId],
                    securityGroups: [],
                    assignPublicIp: "ENABLED",
                },
            },
        };

        const command = new RunTaskCommand(params);
        const response = await ecsClient.send(command);

        let syncFinished = false;
        while (!syncFinished) {
            const taskArn = response.tasks[0].taskArn;
            const probeCommand = new DescribeTasksCommand({
                cluster: clusterArn,
                tasks: [taskArn],
            });
            const probeResponse = await ecsClient.send(probeCommand);
            const task = probeResponse.tasks[0];
            if (task.lastStatus === "STOPPED") {
                syncFinished = true;
                return;
            } else {
                console.log("Data sync task still running...");
                await sleep(5000); // Wait for 5 seconds before the next check
            }
        }
    } catch (err) {
        console.error("Error executing ECS Fargate task:", err);
    }
}

export async function syncImageData(dataImage, lambdaClient) {
    try {
        const params = {
            FunctionName: "HyperflowDataImageUpdater",
            InvocationType: "RequestResponse",
            Payload: JSON.stringify({
                taskDefinition: "hyperflow-data-provider",
                containerName: "data",
                dataImage,
            }),
        };
        const data = await lambdaClient.send(new InvokeCommand(params));
        console.log("Lambda function invoked successfully:", data);
    } catch (err) {
        console.error("Error invoking Lambda function:", err);
    }
}

export async function createLambdaFunction(lambdaClient, roleArn) {
    try {
        const zipFile = fs.readFileSync("lambda.zip");
        const functionParams = {
            Code: {
                ZipFile: zipFile,
            },
            FunctionName: "HyperflowDataImageUpdater",
            Handler: "lambda_function.lambda_handler",
            Role: roleArn,
            Runtime: "python3.10",
            Timeout: 30,
        };

        const data = await lambdaClient.send(
            new CreateFunctionCommand(functionParams),
        );

        let isReady = data.State === "Active";
        while (!isReady) {
            const probeCommand = new GetFunctionCommand({
                FunctionName: "HyperflowDataImageUpdater",
            });
            const response = await lambdaClient.send(probeCommand);
            isReady = response.Configuration.State === "Active";
            await sleep(5000); // Wait for 5 seconds before the next check
        }

        console.log("Lambda function created successfully:", data);
        return data;
    } catch (err) {
        console.error("Error creating Lambda function:", err);
        return { FunctionName: "HyperflowDataImageUpdater" };
    }
}
