import {
    CreateFileSystemCommand,
    CreateMountTargetCommand,
    DescribeFileSystemsCommand,
    DescribeMountTargetsCommand,
} from "@aws-sdk/client-efs";

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function createEFSFileSystem(efsClient, vpcData) {
    try {
        const fileSystemParams = {
            CreationToken: `hyperflow-efs-volume-${Date.now()}`,
            PerformanceMode: "generalPurpose",
            Encrypted: false,
        };

        const fileSystemData = await efsClient.send(
            new CreateFileSystemCommand(fileSystemParams),
        );
        await waitForFileSystemAvailable(
            efsClient,
            fileSystemData.FileSystemId,
        );
        console.log("EFS file system created successfully:", fileSystemData);

        for (let i = 0; i < vpcData.subnets.length; i++) {
            const subnet = vpcData.subnets[i];
            const mountTargetParams = {
                FileSystemId: fileSystemData.FileSystemId,
                SubnetId: subnet.SubnetId,
                SecurityGroups: [vpcData.securityGroup.GroupId],
            };
            const mountTargetData = await efsClient.send(
                new CreateMountTargetCommand(mountTargetParams),
            );
        }
        const probeParams = {
            FileSystemId: fileSystemData.FileSystemId,
        };
        let ready = false;
        while (!ready) {
            const data = await efsClient.send(
                new DescribeMountTargetsCommand(probeParams),
            );
            if (data.MountTargets[0].LifeCycleState === "available") {
                ready = true;
            }
            await sleep(5000);
        }
        console.log("Mount targets created successfully");

        return fileSystemData;
    } catch (err) {
        console.error("Error creating EFS file system or mount target:", err);
    }
}

async function waitForFileSystemAvailable(efsClient, fileSystemId) {
    let isAvailable = false;

    while (!isAvailable) {
        const describeParams = {
            FileSystemId: fileSystemId,
        };

        const data = await efsClient.send(
            new DescribeFileSystemsCommand(describeParams),
        );
        const fs = data.FileSystems[0];
        if (fs.LifeCycleState === "available") {
            isAvailable = true;
            return;
        } else {
            console.log("Waiting for file system to become available...");
            await sleep(5000); // Wait for 5 seconds before the next check
        }
    }
}
