"use strict";
const childProcess = require("child_process");
const fs = require("fs");
const async = require("async");
const aws = require("aws-sdk");
const log4js = require("log4js");
const shortid = require("shortid");
const pidusage = require("pidusage");
const { ProcfsError, procfs } = require("@stroncium/procfs");

aws.config.update({ region: "us-east-1" });

const s3 = new aws.S3();

function getFileSizeObj(fileName, filePath) {
  var size = -1;
  try {
    var stats = fs.statSync(filePath);
    size = stats["size"];
  } catch (err) {}
  return { [fileName]: size };
}

const logProcInfo = function (pid, jm) {
  const logger = log4js.getLogger("hftrace");
  // log process command line
  try {
    let cmdInfo = {
      pid: pid,
      name: jm["executable"],
      command: procfs.processCmdline(pid),
    };
    logger.info("command:", JSON.stringify(cmdInfo));
  } catch (error) {
    if (error.code === ProcfsError.ERR_NOT_FOUND) {
      console.error(`process ${pid} does not exist`);
    }
  }

  // periodically log process IO
  const logProcIO = function (pid) {
    try {
      let ioInfo = procfs.processIo(pid);
      ioInfo.pid = pid;
      ioInfo.name = jm["name"];
      logger.info("IO:", JSON.stringify(ioInfo));
      setTimeout(() => logProcIO(pid), 500);
    } catch (error) {
      if (error.code === ProcfsError.ERR_NOT_FOUND) {
        console.error(`process ${pid} does not exist (this is okay)`);
      }
    }
  };
  logProcIO(pid);

  const logProcNetDev = function (pid) {
    try {
      let netDevInfo = procfs.processNetDev(pid);
      //netDevInfo.pid = pid;
      //netDevInfo.name = jm["name"];
      logger.info("NetDev: pid:", pid, JSON.stringify(netDevInfo));
      setTimeout(() => logProcNetDev(pid), 500);
    } catch (error) {
      if (error.code === ProcfsError.ERR_NOT_FOUND) {
        //console.error(`process ${pid} does not exist (this is okay)`);
      }
    }
  };
  logProcNetDev(pid);

  const logPidUsage = function (pid) {
    pidusage(pid, function (err, stats) {
      if (err) {
        console.error(`pidusage error ${err.code} for process ${pid}`);
        return;
      }
      //console.log(stats);
      // => {
      //   cpu: 10.0,            // percentage (from 0 to 100*vcore)
      //   memory: 357306368,    // bytes
      //   ppid: 312,            // PPID
      //   pid: 727,             // PID
      //   ctime: 867000,        // ms user + system time
      //   elapsed: 6650000,     // ms since the start of the process
      //   timestamp: 864000000  // ms since epoch
      // }
      logger.info("Procusage: pid:", pid, JSON.stringify(stats));
      setTimeout(() => logPidUsage(pid), 500);
    });
  };
  logPidUsage(pid);
};

function handleRequest(request) {
  const metrics = {
    fargateStart: Date.now(),
    fargateEnd: "",
    downloadStart: "",
    downloadEnd: "",
    executionStart: "",
    executionEnd: "",
    uploadStart: "0",
    uploadEnd: "0",
  };

  const executable = request.executable;
  const args = request.args;
  const bucket_name = request.options.bucket;
  const prefix = request.options.prefix;
  const inputs = request.inputs.map((input) => input.name);
  const outputs = request.outputs.map((output) => output.name);
  const files = inputs.slice();
  const logName = request.logName;
  const handlerId = shortid.generate();
  const taskId = request.taskId;
  const name = request.name;

  const logDir = "/mnt/data/logs-hf/";
  const logname =
    "task-" + taskId.replace(/:/g, "__") + "@" + handlerId + ".log";
  const logFilename = logDir + logname;

  log4js.configure({
    appenders: { hftrace: { type: "file", filename: logFilename } },
    categories: { default: { appenders: ["hftrace"], level: "info" } },
  });

  const logger = log4js.getLogger("hftrace");

  logger.info("Environment variables (HF_LOG):" + JSON.stringify(request.env));

  logger.info("handler started, (ID: " + handlerId + ")");

  logger.info("jobMessage: " + JSON.stringify(request));

  files.push(executable);

  console.log("Executable: " + executable);
  console.log("Arguments:  " + args);
  console.log("Inputs:     " + inputs);
  console.log("Outputs:    " + outputs);
  console.log("Bucket:     " + bucket_name);
  console.log("Prefix:     " + prefix);
  console.log("Stdout:     " + request.stdout);

  async.waterfall([download, execute], async function (err) {
    if (err) {
      console.error("Error: " + err);
      process.exit(1);
    } else {
      console.log("Success");
      metrics.fargateEnd = Date.now();
      const metricsString =
        "fargate start: " +
        metrics.fargateStart +
        " fargate end: " +
        metrics.fargateEnd +
        " download start: " +
        metrics.downloadStart +
        " download end: " +
        metrics.downloadEnd +
        " execution start: " +
        metrics.executionStart +
        " execution end: " +
        metrics.executionEnd +
        " upload start: " +
        metrics.uploadStart +
        " upload end: " +
        metrics.uploadEnd;
      console.log(metricsString);
    }
  });
  // console.log("handleRequest")

  function download(callback) {
    // console.log("download start")
    metrics.downloadStart = Date.now();
    async.each(
      files,
      function (file, callback) {
        if (file.endsWith(".js") || file.endsWith(".sh")) {
          console.log("Downloading " + bucket_name + "/" + prefix + "/" + file);

          const params = {
            Bucket: bucket_name,
            Key: prefix + "/" + file,
          };
          s3.getObject(params, function (err, data) {
            if (err) {
              console.log("Error downloading file " + JSON.stringify(params));
              process.exit(1);
            } else {
              const path = "/mnt/data/" + file;
              fs.writeFile(path, data.Body, function (err) {
                if (err) {
                  console.log("Unable to save file " + path);
                  process.exit(1);
                }
                console.log("Downloaded " + path);
                console.log("Downloaded and saved file " + path);
                callback();
              });
            }
          });
        } else {
          // console.log("local file " + file)
          if (callback) {
            callback();
          }
        }
      },
      function (err) {
        metrics.downloadEnd = Date.now();
        if (err) {
          console.error("Failed to download file:" + err);
          process.exit(1);
        } else {
          console.log("All files have been downloaded successfully");
          callback();
        }
      }
    );
  }

  function execute(callback) {
    metrics.executionStart = Date.now();
    let proc_name = executable;
    if (
      executable.endsWith(".js") ||
      executable.endsWith(".sh") ||
      executable.endsWith(".py")
    ) {
      // console.log("remote executable " + executable)
      fs.chmodSync(proc_name, "777");
    }

    let proc;
    console.log("Running executable" + proc_name);
    logger.info("Job command: '" + proc_name + " " + args.join(" ") + "'");

    if (proc_name.endsWith(".js")) {
      proc = childProcess.fork(proc_name, args, { cwd: "/mnt/data/" });
    } else if (proc_name.endsWith(".jar")) {
      let java_args = ["-jar", proc_name];
      const program_args = java_args.concat(args);
      proc = childProcess.spawn("java", program_args, { cwd: "/mnt/data/" });
    } else {
      proc = childProcess.exec(proc_name + " " + args.join(" "), {
        cwd: "/mnt/data/",
      });
      proc.stdout.on("data", function (exedata) {
        console.log("Stdout: " + executable + exedata);
      });

      proc.stderr.on("data", function (exedata) {
        console.log("Stderr: " + executable + exedata);
      });
    }
    logProcInfo(proc.pid, request);
    logger.info("job started: ", request["name"]);

    if (request.stdout) {
      let stdoutStream = fs.createWriteStream("/mnt/data/" + request.stdout, {
        flags: "w",
      });
      proc.stdout.pipe(stdoutStream);
    }

    proc.on("error", function (code) {
      console.error("Error!!" + executable + JSON.stringify(code));
    });
    proc.on("exit", function () {
      console.log("My exe exit " + executable);
      logger.info("job successful (try 1): ", request["name"]);
    });

    proc.on("close", function (code, signal) {
      console.log(
        `Child process closed with code ${code} and signal ${signal}`
      );
      metrics.executionEnd = Date.now();

      logger.info("job exit code:", code);
      if (code === null) {
        logger.error("job failed: ", request["name"]);
        process.exit(1);
      }
      callback();
    });
  }

  function upload(callback) {
    console.log("data: ", fs.readdirSync("/mnt/data"));
    const inputsLog = inputs.map((file) =>
      getFileSizeObj(file, `/mnt/data/${file}`)
    );
    const outputsLog = outputs.map((file) =>
      getFileSizeObj(file, `/mnt/data/${file}`)
    );
    logger.info("Job inputs:", JSON.stringify(inputsLog));
    logger.info("Job outputs:", JSON.stringify(outputsLog));
    logger.info("handler exiting");
    new Promise((resolve) => setTimeout(resolve, 200)).then(() => {
      fs.readFile(logFilename, function (err, data) {
        if (err) {
          console.log("Error reading file " + path);
          // process.exit(1);
        }
        const params = {
          Bucket: bucket_name,
          Key: prefix + "/logs-hf/" + logname,
          Body: data,
        };
        s3.putObject(params, function (err) {
          if (err) {
            console.log("Error uploading file " + logname);
            // process.exit(1);
          }
          console.log("Uploaded file " + logname);
        });
      });
    });
    metrics.uploadStart = Date.now();
    async.each(
      outputs,
      function (file, callback) {},
      function (err) {
        metrics.uploadEnd = Date.now();
        if (err) {
          console.log("Error uploading file " + err);
          process.exit(1);
        } else {
          console.log("All files have been uploaded successfully");
          callback();
        }
      }
    );
  }
}

let arg = process.argv[2];

if (!arg) {
  console.log("Received empty request, exiting...");
  process.exit(1);
}

if (arg.startsWith("S3")) {
  const params = JSON.parse(arg.split("=")[1]);
  console.log("Getting executable config from S3: " + JSON.stringify(params));
  s3.getObject(params, function (err, data) {
    if (err) return err;
    arg = data.Body.toString();
    handleRequest(JSON.parse(arg));
  });
} else {
  handleRequest(JSON.parse(arg));
}
