import { CreateClusterCommand } from "@aws-sdk/client-ecs";

import { RegisterTaskDefinitionCommand } from "@aws-sdk/client-ecs";
const CPU_DEFAULT = "1024";
const MEMORY_DEFAULT = "2048";


const TASK_DEFINITION_BASE = {
    networkMode: "awsvpc",
    requiresCompatibilities: ["FARGATE"],
    cpu: CPU_DEFAULT,
    memory: MEMORY_DEFAULT,
    runtimePlatform: {
        cpuArchitecture: "X86_64",
        operatingSystemFamily: "LINUX",
    },
};

function getLogConfiguration(name) {
    return {
        logDriver: "awslogs",
        options: {
            "awslogs-group": `/ecs/${name}`,
            "awslogs-create-group": "true",
            "awslogs-region": "us-east-1",
            "awslogs-stream-prefix": "ecs",
        },
        secretOptions: [],
    };
}

async function createDataProviderTaskDefinition(ecsClient, efsData, roleArn, image) {
    const taskDefinition = {
        ...TASK_DEFINITION_BASE,
        family: "hyperflow-data-provider",
        taskRoleArn: roleArn,
        executionRoleArn: roleArn,
        containerDefinitions: [
            {
                name: "data",
                image,
                cpu: 0,
                portMappings: [
                    {
                        name: "data-80-tcp",
                        containerPort: 80,
                        hostPort: 80,
                        protocol: "tcp",
                        appProtocol: "http",
                    },
                ],
                essential: false,
                environment: [],
                environmentFiles: [],
                mountPoints: [],
                volumesFrom: [],
                ulimits: [],
                logConfiguration: getLogConfiguration("data"),
                systemControls: [],
            },
            {
                name: "script",
                image: "339712938475.dkr.ecr.us-east-1.amazonaws.com/1000genome-workflow-data-provider:latest",
                cpu: 0,
                portMappings: [],
                essential: true,
                environment: [],
                environmentFiles: [],
                mountPoints: [
                    {
                        sourceVolume: "efsdata",
                        containerPath: "/mnt/data",
                        readOnly: false,
                    },
                ],
                volumesFrom: [
                    {
                        sourceContainer: "data",
                        readOnly: true,
                    },
                ],
                systemControls: [],
                logConfiguration: getLogConfiguration("data-provider-script"),
            },
        ],
        volumes: [
            {
                name: "efsdata",
                efsVolumeConfiguration: {
                    fileSystemId: efsData.FileSystemId,
                    rootDirectory: "/",
                },
            },
        ],
    };
    try {
        const data = await ecsClient.send(
            new RegisterTaskDefinitionCommand(taskDefinition),
        );

        console.log(
            "Data provider worker task definition registered successfully:",
            data.taskDefinition,
        );
        return data.taskDefinition;
    } catch (err) {
        console.error("Error registering data provider task definition:", err);
    }
}

async function createWorkerTaskDefinition(ecsClient, efsData, roleArn, image) {
    const taskDefinition = {
        ...TASK_DEFINITION_BASE,
        family: "hyperflow-worker",
        taskRoleArn: roleArn,
        executionRoleArn: roleArn,
        containerDefinitions: [
            {
                name: "worker",
                image,
                cpu: 0,
                portMappings: [
                    {
                        name: "hyperflow-worker-80-tcp",
                        containerPort: 80,
                        hostPort: 80,
                        protocol: "tcp",
                        appProtocol: "http",
                    },
                ],
                essential: true,
                environment: [],
                environmentFiles: [],
                mountPoints: [
                    {
                        sourceVolume: "efs-volume",
                        containerPath: "/mnt/data",
                        readOnly: false,
                    },
                ],
                volumesFrom: [],
                ulimits: [],
                logConfiguration: getLogConfiguration("hyperflow-worker"),
                systemControls: [],
            },
        ],
        volumes: [
            {
                name: "efs-volume",
                efsVolumeConfiguration: {
                    fileSystemId: efsData.FileSystemId,
                    rootDirectory: "/",
                },
            },
        ],
    };

    try {
        const data = await ecsClient.send(
            new RegisterTaskDefinitionCommand(taskDefinition),
        );

        console.log(
            "Worker task definition registered successfully:",
            data.taskDefinition,
        );
        return data.taskDefinition;
    } catch (err) {
        console.error("Error registering worker task definition:", err);
    }
}

export async function createTaskDefinitions(ecsClient, efsData, roleArn, workerImage, dataProviderImage) {
    const [workerTaskDefinition, dataProviderTaskDefinition] =
        await Promise.all([
            createWorkerTaskDefinition(ecsClient, efsData, roleArn, workerImage),
            createDataProviderTaskDefinition(ecsClient, efsData, roleArn, dataProviderImage),
        ]);
    return {
        worker: workerTaskDefinition,
        dataProvider: dataProviderTaskDefinition,
    };
}

export async function setupCluster(ecsClient) {
    // Define cluster parameters
    const params = {
        clusterName: "hyperflow-cluster",
    };

    try {
        // Create the cluster
        const data = await ecsClient.send(new CreateClusterCommand(params));
        console.log("Cluster created successfully:", data.cluster);
        return data.cluster;
    } catch (err) {
        console.error("Error creating cluster:", err);
    }
}
